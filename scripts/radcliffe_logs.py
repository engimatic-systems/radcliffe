#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from google.protobuf.message import DecodeError
from radcliffe_protocol.radcliffe.v2.protocol_pb2 import MessageEnvelope
from radcliffe_services import ServiceManager, selected_services

ROOT = Path(__file__).resolve().parent.parent
STATIC_LOGS = {
    "control": "var/control-plane.log",
    "sidecar": "var/agent-sidecar.log",
    "compat_worker": "var/media-worker.log",
    "telemetry": "var/telemetry/events.jsonl",
    "diagnostic_bundle": "artifacts/diagnostics/bundle.json",
}
_CREDENTIAL_PATTERNS = {
    "authorization_value": re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+\S+"),
    "device_credential": re.compile(rb"rdc_dev_[A-Za-z0-9_-]{20,}"),
    "provider_credential": re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,})"),
}


@dataclass(frozen=True, slots=True)
class LogFile:
    logical_name: str
    path: Path
    rotatable: bool


@dataclass(frozen=True, slots=True)
class Violation:
    logical_name: str
    code: str


def discover(root: Path, *, include_rotated: bool = False) -> tuple[LogFile, ...]:
    files = [
        LogFile(name, root / relative, name != "diagnostic_bundle")
        for name, relative in STATIC_LOGS.items()
        if (root / relative).is_file()
    ]
    workers = root / "var/workers"
    if workers.is_dir():
        files.extend(
            LogFile("managed_worker", path, True)
            for path in sorted(workers.glob("*.log"))
            if path.is_file()
        )
    if include_rotated:
        for name, relative in STATIC_LOGS.items():
            base = root / relative
            files.extend(
                LogFile(name, candidate, False)
                for candidate in sorted(base.parent.glob(f"{base.name}.*"))
                if candidate.is_file() and candidate.name.removeprefix(f"{base.name}.").isdigit()
            )
        if workers.is_dir():
            files.extend(
                LogFile("managed_worker", candidate, False)
                for candidate in sorted(workers.glob("*.log.*"))
                if candidate.is_file() and candidate.name.rsplit(".", 1)[-1].isdigit()
            )
    return tuple(files)


def secret_values(environment: Mapping[str, str]) -> tuple[bytes, ...]:
    markers = ("TOKEN", "SECRET", "API_KEY", "CREDENTIAL")
    return tuple(
        value.encode()
        for key, value in environment.items()
        if len(value) >= 8 and any(marker in key.upper() for marker in markers)
    )


def private_text_values(database_path: Path, source_root: Path) -> tuple[bytes, ...]:
    values: set[bytes] = set()
    if database_path.is_file():
        try:
            database = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
            try:
                rows = database.execute("SELECT envelope FROM envelopes").fetchall()
            finally:
                database.close()
            for row in rows:
                envelope = MessageEnvelope.FromString(row[0])
                if envelope.content.HasField("inline_text"):
                    encoded = envelope.content.inline_text.encode()
                    if len(encoded) >= 8:
                        values.add(encoded)
        except (sqlite3.Error, DecodeError, ValueError) as error:
            raise RuntimeError("durable message privacy index is unavailable") from error
    if source_root.is_dir():
        for path in source_root.rglob("*.md"):
            try:
                encoded = path.read_bytes()
            except OSError as error:
                raise RuntimeError("source privacy index is unavailable") from error
            if 8 <= len(encoded) <= 2_000_000:
                values.add(encoded)
            values.update(line for line in encoded.splitlines() if len(line) >= 16)
    return tuple(values)


def scan(
    files: Iterable[LogFile],
    *,
    forbidden_values: Iterable[bytes],
) -> tuple[Violation, ...]:
    forbidden = tuple(value for value in forbidden_values if len(value) >= 8)
    violations: set[Violation] = set()
    for item in files:
        try:
            payload = item.path.read_bytes()
            mode = item.path.stat().st_mode & 0o777
        except OSError:
            violations.add(Violation(item.logical_name, "unreadable"))
            continue
        if mode & 0o077:
            violations.add(Violation(item.logical_name, "insecure_mode"))
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            violations.add(Violation(item.logical_name, "binary_or_microphone_audio"))
        if b"\x00" in payload:
            violations.add(Violation(item.logical_name, "binary_or_microphone_audio"))
        for code, pattern in _CREDENTIAL_PATTERNS.items():
            if pattern.search(payload):
                violations.add(Violation(item.logical_name, code))
        if any(value in payload for value in forbidden):
            violations.add(Violation(item.logical_name, "private_content_or_secret"))
    return tuple(sorted(violations, key=lambda item: (item.logical_name, item.code)))


def purge_private_files(
    files: Iterable[LogFile],
    *,
    forbidden_values: Iterable[bytes],
) -> int:
    forbidden = tuple(value for value in forbidden_values if len(value) >= 8)
    removed = 0
    for item in files:
        try:
            payload = item.path.read_bytes()
        except OSError:
            continue
        credential_found = any(pattern.search(payload) for pattern in _CREDENTIAL_PATTERNS.values())
        private_found = any(value in payload for value in forbidden)
        binary_found = b"\x00" in payload
        if not binary_found:
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError:
                binary_found = True
        if credential_found or private_found or binary_found:
            item.path.unlink(missing_ok=True)
            removed += 1
    return removed


def enforce_permissions(root: Path) -> None:
    for directory in (root / "var", root / "var/workers", root / "var/telemetry"):
        if directory.is_dir():
            directory.chmod(0o700)
    for item in discover(root, include_rotated=True):
        item.path.chmod(0o600)


def rotated(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def rotate_file(path: Path, *, maximum_bytes: int, keep: int) -> bool:
    if maximum_bytes < 1 or keep < 1:
        raise ValueError("rotation size and retained count must be positive")
    if not path.is_file() or path.stat().st_size < maximum_bytes:
        return False
    rotated(path, keep).unlink(missing_ok=True)
    for index in range(keep - 1, 0, -1):
        source = rotated(path, index)
        if source.exists():
            source.replace(rotated(path, index + 1))
    path.replace(rotated(path, 1))
    rotated(path, 1).chmod(0o600)
    return True


def prune(root: Path, *, keep: int, maximum_age_days: int, now: float | None = None) -> int:
    if keep < 1 or maximum_age_days < 1:
        raise ValueError("retention count and age must be positive")
    instant = time.time() if now is None else now
    cutoff = instant - maximum_age_days * 86_400
    removed = 0
    for item in discover(root, include_rotated=True):
        if item.rotatable:
            continue
        suffix = item.path.name.rsplit(".", 1)[-1]
        if suffix.isdigit() and (int(suffix) > keep or item.path.stat().st_mtime < cutoff):
            item.path.unlink()
            removed += 1
    workers = root / "var/workers"
    if workers.is_dir():
        for path in workers.glob("*.log"):
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    return removed


def maintain(
    root: Path,
    *,
    maximum_bytes: int,
    keep: int,
    maximum_age_days: int,
    manager: ServiceManager | None,
    forbidden_values: Iterable[bytes] = (),
) -> dict[str, int]:
    services = selected_services(include_compat_worker=True)
    if manager is not None:
        manager.stop(services)
    try:
        purged = purge_private_files(
            discover(root, include_rotated=True),
            forbidden_values=forbidden_values,
        )
        rotated_count = sum(
            rotate_file(item.path, maximum_bytes=maximum_bytes, keep=keep)
            for item in discover(root)
            if item.rotatable
        )
        removed = prune(root, keep=keep, maximum_age_days=maximum_age_days)
        enforce_permissions(root)
    finally:
        if manager is not None:
            manager.start(services)
    return {"rotated": rotated_count, "removed": removed, "purged": purged}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Maintain and audit private Radcliffe logs")
    result.add_argument("command", choices=("audit", "maintain"))
    result.add_argument("--maximum-bytes", type=int, default=10 * 1024 * 1024)
    result.add_argument("--keep", type=int, default=5)
    result.add_argument("--maximum-age-days", type=int, default=14)
    result.add_argument("--no-services", action="store_true", help=argparse.SUPPRESS)
    return result


def main() -> int:
    arguments = parser().parse_args()
    database = Path(os.environ.get("RADCLIFFE_DATABASE_PATH", "var/radcliffe.sqlite3"))
    source_root = Path(os.environ.get("RADCLIFFE_SOURCE_ROOT", "fixtures/sources"))
    database = database if database.is_absolute() else ROOT / database
    source_root = source_root if source_root.is_absolute() else ROOT / source_root
    try:
        forbidden_values = (
            *secret_values(os.environ),
            *private_text_values(database, source_root),
        )
    except RuntimeError:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "violations": [{"logical_name": "privacy_index", "code": "unavailable"}],
                },
                sort_keys=True,
            )
        )
        return 1
    if arguments.command == "maintain":
        manager = None
        if not arguments.no_services:
            if sys.platform != "darwin":
                raise RuntimeError("supervised log maintenance requires macOS")
            manager = ServiceManager(
                root=ROOT,
                launch_agents=Path.home() / "Library/LaunchAgents",
                uid=os.getuid(),
            )
        result = maintain(
            ROOT,
            maximum_bytes=arguments.maximum_bytes,
            keep=arguments.keep,
            maximum_age_days=arguments.maximum_age_days,
            manager=manager,
            forbidden_values=forbidden_values,
        )
        print(json.dumps({"status": "maintained", **result}, sort_keys=True))
        return 0
    violations = scan(
        discover(ROOT, include_rotated=True),
        forbidden_values=forbidden_values,
    )
    print(
        json.dumps(
            {
                "status": "clean" if not violations else "rejected",
                "violations": [asdict(item) for item in violations],
            },
            sort_keys=True,
        )
    )
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
