#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from radcliffe_services import ServiceManager, ServiceStatus, selected_services

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class HttpObservation:
    status_code: int | None
    payload: object | None


@dataclass(frozen=True, slots=True)
class OperationalCheck:
    component: str
    status: str
    code: str
    details: Mapping[str, object]


HttpFetcher = Callable[[str, bool], HttpObservation]


def safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        return "invalid"
    try:
        port = parsed.port
    except ValueError:
        return "invalid"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def livekit_http_url(value: str) -> str:
    endpoint = safe_endpoint(value)
    parsed = urlsplit(endpoint)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme)
    return urlunsplit((scheme or "invalid", parsed.netloc, "/", "", ""))


def database_check(database_path: Path, expected_schema: int) -> OperationalCheck:
    if not database_path.is_file():
        return OperationalCheck("database", "failed", "database_missing", {})
    try:
        database = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=1)
        try:
            integrity = database.execute("PRAGMA quick_check").fetchone()
            schema = database.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        finally:
            database.close()
    except (OSError, sqlite3.Error):
        return OperationalCheck("database", "failed", "database_unavailable", {})
    if integrity is None or integrity[0] != "ok":
        return OperationalCheck("database", "failed", "integrity_failed", {})
    observed = int(schema[0]) if schema is not None and schema[0] is not None else 0
    if observed != expected_schema:
        return OperationalCheck(
            "database",
            "failed",
            "schema_mismatch",
            {"schema_version": observed, "expected_schema_version": expected_schema},
        )
    return OperationalCheck(
        "database",
        "ok",
        "schema_current",
        {"schema_version": observed, "bytes": database_path.stat().st_size},
    )


def latest_schema_version(root: Path) -> int:
    migrations = root / "packages/domain-python/src/radcliffe_domain/migrations"
    versions = (
        int(path.name.partition("_")[0])
        for path in migrations.glob("*.sql")
        if path.name.partition("_")[0].isdigit()
    )
    return max(versions)


def fetch(url: str, require_json: bool) -> HttpObservation:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=2) as response:  # noqa: S310 - operator-selected endpoints
            status_code = response.status
            body = response.read(256_000)
    except HTTPError as error:
        status_code = error.code
        body = error.read(256_000)
    except (OSError, URLError, ValueError):
        return HttpObservation(None, None)
    if not require_json:
        return HttpObservation(status_code, None)
    try:
        return HttpObservation(status_code, json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpObservation(status_code, None)


def http_check(
    component: str,
    observation: HttpObservation,
    *,
    expected_status: str | None = None,
) -> OperationalCheck:
    payload = observation.payload if isinstance(observation.payload, dict) else {}
    if observation.status_code is not None and 200 <= observation.status_code < 300:
        if expected_status is None or payload.get("status") == expected_status:
            details = {
                key: int(value)
                for key, value in payload.items()
                if key in {"active_turns", "cancelled_turns"}
                and isinstance(value, int)
                and not isinstance(value, bool)
            }
            return OperationalCheck(component, "ok", f"{component}_ready", details)
    return OperationalCheck(component, "failed", f"{component}_unavailable", {})


def readiness_check(observation: HttpObservation) -> OperationalCheck:
    payload = observation.payload if isinstance(observation.payload, dict) else {}
    raw_checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    safe_checks = []
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        safe_checks.append(
            {
                key: item[key]
                for key in ("component", "status", "code", "observed", "expected")
                if key in item and isinstance(item[key], str | int)
            }
        )
    ready = observation.status_code == 200 and payload.get("status") == "ready"
    return OperationalCheck(
        "control_readiness",
        "ok" if ready else "failed",
        "dependencies_ready" if ready else "dependencies_not_ready",
        {"checks": safe_checks},
    )


def service_checks(statuses: Sequence[ServiceStatus]) -> tuple[OperationalCheck, ...]:
    return tuple(
        OperationalCheck(
            f"service_{status.name}",
            "ok" if status.loaded and status.state == "running" else "failed",
            "service_running" if status.loaded and status.state == "running" else "service_down",
            {
                "installed": status.installed,
                "loaded": status.loaded,
                "pid": status.pid,
                "last_exit_code": status.last_exit_code,
                "optional": status.name == "compat-worker",
            },
        )
        for status in statuses
    )


def resource_check(root: Path) -> tuple[OperationalCheck, dict[str, object]]:
    var = root / "var"
    var.mkdir(exist_ok=True)
    usage = shutil.disk_usage(var)
    free_fraction = usage.free / usage.total if usage.total else 0
    status = "warning" if usage.free < 1_000_000_000 or free_fraction < 0.05 else "ok"
    logs = {
        name: path.stat().st_size if path.is_file() else 0
        for name, path in {
            "control": var / "control-plane.log",
            "sidecar": var / "agent-sidecar.log",
            "compat_worker": var / "media-worker.log",
        }.items()
    }
    check = OperationalCheck(
        "disk",
        status,
        "disk_pressure" if status == "warning" else "disk_available",
        {"free_bytes": usage.free, "total_bytes": usage.total},
    )
    return check, {"log_bytes": logs}


def collect_report(
    *,
    root: Path,
    environment: Mapping[str, str],
    statuses: Sequence[ServiceStatus],
    http_fetcher: HttpFetcher = fetch,
    revision: str = "unknown",
    dirty: bool = False,
    versions: Mapping[str, str] | None = None,
) -> dict[str, object]:
    control_url = safe_endpoint(environment.get("RADCLIFFE_CONTROL_URL", "http://127.0.0.1:8000"))
    sidecar_url = safe_endpoint(environment.get("RADCLIFFE_SIDECAR_URL", "http://127.0.0.1:8787"))
    livekit_url = safe_endpoint(environment.get("RADCLIFFE_LIVEKIT_URL", "ws://127.0.0.1:7880"))
    raw_database = Path(environment.get("RADCLIFFE_DATABASE_PATH", "var/radcliffe.sqlite3"))
    database_path = raw_database if raw_database.is_absolute() else root / raw_database
    checks: list[OperationalCheck] = list(service_checks(statuses))
    checks.extend(
        (
            http_check(
                "control_liveness",
                http_fetcher(f"{control_url}/healthz", True),
                expected_status="ok",
            ),
            readiness_check(http_fetcher(f"{control_url}/readyz", True)),
            http_check(
                "sidecar",
                http_fetcher(f"{sidecar_url}/healthz", True),
                expected_status="ok",
            ),
            http_check("livekit", http_fetcher(livekit_http_url(livekit_url), False)),
            database_check(database_path, latest_schema_version(root)),
        )
    )
    disk, resources = resource_check(root)
    checks.append(disk)
    required_failures = [
        check
        for check in checks
        if check.status == "failed" and not bool(check.details.get("optional"))
    ]
    return {
        "schema": "radcliffe-operations-status-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "ready" if not required_failures else "not_ready",
        "revision": revision,
        "dirty": dirty,
        "endpoints": {
            "control": control_url,
            "sidecar": sidecar_url,
            "livekit": livekit_url,
        },
        "versions": dict(versions or {}),
        "checks": [asdict(check) for check in checks],
        "resources": resources,
    }


def sensitive_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    markers = ("TOKEN", "SECRET", "API_KEY", "CREDENTIAL")
    return tuple(
        value
        for key, value in environment.items()
        if value and len(value) >= 8 and any(marker in key.upper() for marker in markers)
    )


def serialize_report(report: Mapping[str, object], environment: Mapping[str, str]) -> str:
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if any(secret in encoded for secret in sensitive_values(environment)):
        raise RuntimeError("refusing to emit an operational report containing a configured secret")
    return encoded + "\n"


def render_human(report: Mapping[str, object]) -> str:
    lines = [f"Radcliffe operations: {report['status']}", f"Revision: {report['revision']}"]
    checks = report.get("checks")
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"[{str(item.get('status', 'unknown')).upper():7}] "
                f"{item.get('component')}: {item.get('code')}"
            )
    return "\n".join(lines) + "\n"


def command_output(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Inspect Radcliffe local operational readiness")
    result.add_argument("--json", action="store_true", help="print the redacted JSON report")
    result.add_argument("--output", type=Path, help="write the redacted JSON report with mode 0600")
    result.add_argument(
        "--wait",
        type=float,
        default=0,
        help="wait up to this many seconds for ready",
    )
    result.add_argument("--interval", type=float, default=0.5, help=argparse.SUPPRESS)
    return result


def main() -> int:
    arguments = parser().parse_args()
    manager = ServiceManager(
        root=ROOT,
        launch_agents=Path.home() / "Library/LaunchAgents",
        uid=os.getuid(),
    )
    services = selected_services(include_compat_worker=True)
    deadline = time.monotonic() + max(0, arguments.wait)
    while True:
        report = collect_report(
            root=ROOT,
            environment=os.environ,
            statuses=manager.statuses(services),
            revision=command_output("git", "rev-parse", "--short=12", "HEAD"),
            dirty=bool(command_output("git", "status", "--porcelain")),
            versions={
                "python": sys.version.split()[0],
                "uv": command_output("uv", "--version"),
                "node": command_output(os.environ.get("RADCLIFFE_NODE_BIN", "node"), "--version"),
            },
        )
        if report["status"] == "ready" or time.monotonic() >= deadline:
            break
        time.sleep(max(0.05, arguments.interval))
    encoded = serialize_report(report, os.environ)
    if arguments.output is not None:
        write_private(arguments.output, encoded)
    print(encoded if arguments.json else render_human(report), end="")
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
