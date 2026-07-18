#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

from radcliffe_services import SERVICES, ServiceDefinition, ServiceManager

ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCHEMA = "radcliffe-sqlite-backup-v1"
DEPLOYMENT_SCHEMA = "radcliffe-deployment-receipt-v1"
DATABASE_FILE = "database.sqlite3"
MANIFEST_FILE = "manifest.json"
DATA_TABLES = (
    "schema_migrations",
    "sessions",
    "envelopes",
    "tool_calls",
    "playback_acknowledgements",
    "devices",
    "device_auth_audit",
    "configuration_values",
    "configuration_mutations",
)


class RecoveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DatabaseInventory:
    schema_versions: tuple[int, ...]
    table_rows: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class BackupManifest:
    schema: str
    backup_id: str
    created_at: str
    kind: str
    source_revision: str
    target_revision: str | None
    database_file: str
    database_sha256: str
    database_bytes: int
    inventory: DatabaseInventory


Progress = Callable[[int, int, int], None]
StartupProbe = Callable[[Path], None]
CopyFile = Callable[[Path, Path], object]
DiskFree = Callable[[Path], int]


class RevisionControl(Protocol):
    def current(self) -> str: ...

    def require_clean(self) -> None: ...

    def validate_release(self, revision: str) -> None: ...

    def switch(self, revision: str) -> None: ...


class ServiceControl(Protocol):
    def stop(self, services: Sequence[ServiceDefinition]) -> None: ...

    def install(self, services: Sequence[ServiceDefinition]) -> None: ...


def sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(path: Path, content: str) -> None:
    private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_database(path: Path) -> DatabaseInventory:
    if not path.is_file():
        raise RecoveryError("database is missing")
    try:
        database = sqlite3.connect(sqlite_uri(path), uri=True, timeout=2)
        try:
            integrity = database.execute("PRAGMA quick_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RecoveryError("database integrity check failed")
            foreign_keys = database.execute("PRAGMA foreign_key_check").fetchone()
            if foreign_keys is not None:
                raise RecoveryError("database foreign-key check failed")
            names = {
                str(row[0])
                for row in database.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                ).fetchall()
            }
            if "schema_migrations" not in names:
                raise RecoveryError("database migration history is missing")
            versions = tuple(
                int(row[0])
                for row in database.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            )
            rows = {
                table: int(database.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in DATA_TABLES
                if table in names
            }
        finally:
            database.close()
    except sqlite3.Error as error:
        raise RecoveryError("database is unavailable") from error
    return DatabaseInventory(schema_versions=versions, table_rows=rows)


def latest_schema_version(root: Path = ROOT) -> int:
    migrations = root / "packages/domain-python/src/radcliffe_domain/migrations"
    versions = [
        int(path.name.partition("_")[0])
        for path in migrations.glob("*.sql")
        if path.name.partition("_")[0].isdigit()
    ]
    if not versions:
        raise RecoveryError("current migration set is unavailable")
    return max(versions)


def sensitive_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    markers = ("TOKEN", "SECRET", "API_KEY", "CREDENTIAL")
    return tuple(
        value
        for key, value in environment.items()
        if len(value) >= 8 and any(marker in key.upper() for marker in markers)
    )


def serialize_json(payload: Mapping[str, object], environment: Mapping[str, str]) -> str:
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if any(secret in encoded for secret in sensitive_values(environment)):
        raise RecoveryError("refusing to emit recovery metadata containing a configured secret")
    return encoded + "\n"


def manifest_payload(manifest: BackupManifest) -> dict[str, object]:
    payload = asdict(manifest)
    payload["inventory"] = asdict(manifest.inventory)
    return payload


def parse_manifest(path: Path) -> BackupManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        inventory = payload["inventory"]
        manifest = BackupManifest(
            schema=str(payload["schema"]),
            backup_id=str(payload["backup_id"]),
            created_at=str(payload["created_at"]),
            kind=str(payload["kind"]),
            source_revision=str(payload["source_revision"]),
            target_revision=(
                str(payload["target_revision"])
                if payload.get("target_revision") is not None
                else None
            ),
            database_file=str(payload["database_file"]),
            database_sha256=str(payload["database_sha256"]),
            database_bytes=int(payload["database_bytes"]),
            inventory=DatabaseInventory(
                schema_versions=tuple(int(value) for value in inventory["schema_versions"]),
                table_rows={str(key): int(value) for key, value in inventory["table_rows"].items()},
            ),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RecoveryError("backup manifest is invalid") from error
    if manifest.schema != BACKUP_SCHEMA:
        raise RecoveryError("backup manifest schema is unsupported")
    if manifest.backup_id != path.parent.name or manifest.database_file != DATABASE_FILE:
        raise RecoveryError("backup manifest identity is invalid")
    if not manifest.backup_id.startswith("bkp_"):
        raise RecoveryError("backup identifier is invalid")
    if len(manifest.database_sha256) != 64:
        raise RecoveryError("backup digest is invalid")
    return manifest


def default_startup_probe(database_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
        from radcliffe_control.app import create_app  # type: ignore[import-untyped]
        from radcliffe_control.settings import Settings  # type: ignore[import-untyped]

        source_root = ROOT / "fixtures/sources"
        settings = Settings(
            database_path=database_path,
            source_root=source_root,
            livekit_url="ws://127.0.0.1:7880",
            livekit_api_key="recovery-probe-key",
            livekit_api_secret="recovery-probe-secret-at-least-32-bytes",
            bootstrap_token="recovery-probe-bootstrap-at-least-32-bytes",
            internal_token="recovery-probe-internal-at-least-32-bytes",
            managed_workers_enabled=False,
        )
        try:
            with TestClient(create_app(settings)) as client:
                response = client.get("/healthz")
                if response.status_code != 200 or response.json() != {"status": "ok"}:
                    raise RecoveryError(
                        "current control plane did not start from restored database"
                    )
        except RecoveryError:
            raise
        except Exception as error:
            raise RecoveryError(
                "current control plane did not start from restored database"
            ) from error


def validate_database_copy(
    path: Path,
    manifest: BackupManifest,
    *,
    startup_probe: StartupProbe,
    root: Path,
) -> DatabaseInventory:
    if path.stat().st_size != manifest.database_bytes:
        raise RecoveryError("backup size does not match manifest")
    if file_sha256(path) != manifest.database_sha256:
        raise RecoveryError("backup digest does not match manifest")
    before = inspect_database(path)
    if before != manifest.inventory:
        raise RecoveryError("backup inventory does not match manifest")
    current_schema = latest_schema_version(root)
    if before.schema_versions and max(before.schema_versions) > current_schema:
        raise RecoveryError("backup schema is newer than this release")
    startup_probe(path)
    after = inspect_database(path)
    if not after.schema_versions or max(after.schema_versions) != current_schema:
        raise RecoveryError("restored database did not migrate to the current schema")
    for table, count in before.table_rows.items():
        if after.table_rows.get(table) != count:
            raise RecoveryError("restored database did not preserve its inventory")
    return after


def verify_backup(
    backup_directory: Path,
    *,
    startup_probe: StartupProbe = default_startup_probe,
    root: Path = ROOT,
) -> BackupManifest:
    backup_directory = backup_directory.resolve()
    manifest = parse_manifest(backup_directory / MANIFEST_FILE)
    source = backup_directory / DATABASE_FILE
    with tempfile.TemporaryDirectory(prefix="radcliffe-restore-verify-") as temporary:
        scratch = Path(temporary) / DATABASE_FILE
        shutil.copyfile(source, scratch)
        scratch.chmod(0o600)
        validate_database_copy(
            scratch,
            manifest,
            startup_probe=startup_probe,
            root=root,
        )
    return manifest


def available_disk(path: Path) -> int:
    return shutil.disk_usage(path).free


def backup_identifier(now: datetime | None = None) -> str:
    instant = now or datetime.now(UTC)
    return f"bkp_{instant.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:8]}"


def create_backup(
    database_path: Path,
    backup_root: Path,
    *,
    source_revision: str,
    kind: str = "manual",
    target_revision: str | None = None,
    environment: Mapping[str, str] = os.environ,
    progress: Progress | None = None,
    disk_free: DiskFree = available_disk,
    retain: int | None = 7,
) -> tuple[Path, BackupManifest]:
    database_path = database_path.resolve()
    backup_root = backup_root.resolve()
    if not database_path.is_file():
        raise RecoveryError("source database is missing")
    if retain is not None and retain < 1:
        raise RecoveryError("backup retention must keep at least one backup")
    private_directory(backup_root)
    source_bytes = database_path.stat().st_size
    required = max(source_bytes * 2, 10 * 1024 * 1024)
    if disk_free(backup_root) < required:
        raise RecoveryError("insufficient disk space for a consistent backup")
    backup_id = backup_identifier()
    staging = backup_root / f".pending-{backup_id}"
    final = backup_root / backup_id
    private_directory(staging)
    destination = staging / DATABASE_FILE
    try:
        source = sqlite3.connect(sqlite_uri(database_path), uri=True, timeout=5)
        target = sqlite3.connect(destination)
        try:
            source.backup(target, pages=256, progress=progress)
        finally:
            target.close()
            source.close()
        destination.chmod(0o600)
        fsync_file(destination)
        inventory = inspect_database(destination)
        if not inventory.schema_versions:
            raise RecoveryError("backup has no applied schema migrations")
        manifest = BackupManifest(
            schema=BACKUP_SCHEMA,
            backup_id=backup_id,
            created_at=datetime.now(UTC).isoformat(),
            kind=kind,
            source_revision=source_revision,
            target_revision=target_revision,
            database_file=DATABASE_FILE,
            database_sha256=file_sha256(destination),
            database_bytes=destination.stat().st_size,
            inventory=inventory,
        )
        write_private(
            staging / MANIFEST_FILE,
            serialize_json(manifest_payload(manifest), environment),
        )
        fsync_directory(staging)
        staging.replace(final)
        fsync_directory(backup_root)
    except (OSError, sqlite3.Error) as error:
        raise RecoveryError("SQLite-consistent backup failed") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if retain is not None:
        prune_backups(backup_root, retain=retain, apply=True)
    return final, manifest


def backup_directories(backup_root: Path) -> tuple[Path, ...]:
    candidates: list[tuple[str, Path]] = []
    if not backup_root.is_dir():
        return ()
    for path in backup_root.iterdir():
        if not path.is_dir() or not path.name.startswith("bkp_"):
            continue
        try:
            manifest = parse_manifest(path / MANIFEST_FILE)
            candidates.append((manifest.created_at, path))
        except RecoveryError:
            continue
    return tuple(path for _created, path in sorted(candidates, reverse=True))


def prune_backups(backup_root: Path, *, retain: int, apply: bool) -> tuple[str, ...]:
    if retain < 1:
        raise RecoveryError("backup retention must keep at least one backup")
    stale = backup_directories(backup_root)[retain:]
    if apply:
        for path in stale:
            shutil.rmtree(path)
        if stale:
            fsync_directory(backup_root)
    return tuple(path.name for path in stale)


def restore_backup(
    backup_directory: Path,
    destination: Path,
    *,
    live_database: Path,
    startup_probe: StartupProbe = default_startup_probe,
    root: Path = ROOT,
    copy_file: CopyFile = shutil.copyfile,
) -> BackupManifest:
    destination = destination.resolve()
    if destination == live_database.resolve():
        raise RecoveryError("ordinary restore cannot replace the live database; use rollback")
    manifest = verify_backup(backup_directory, startup_probe=startup_probe, root=root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.restore-", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        copy_file(backup_directory / DATABASE_FILE, temporary)
        temporary.chmod(0o600)
        fsync_file(temporary)
        validate_database_copy(
            temporary,
            manifest,
            startup_probe=startup_probe,
            root=root,
        )
        temporary.replace(destination)
        destination.chmod(0o600)
        fsync_directory(destination.parent)
    except OSError as error:
        raise RecoveryError("restore copy failed") from error
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


class GitRevisionControl:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _run(self, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                ("git", *arguments),
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RecoveryError("revision operation failed") from error
        return completed.stdout.strip()

    def current(self) -> str:
        return self._run("rev-parse", "HEAD")

    def require_clean(self) -> None:
        if self._run("status", "--porcelain", "--untracked-files=no"):
            raise RecoveryError("rollback requires a clean tracked worktree")

    def validate_release(self, revision: str) -> None:
        self._run("cat-file", "-e", f"{revision}^{{commit}}")
        for path in (
            "pyproject.toml",
            "scripts/run-local-control.sh",
            "scripts/run-local-sidecar.sh",
            "scripts/run-local-worker.sh",
        ):
            self._run("cat-file", "-e", f"{revision}:{path}")

    def switch(self, revision: str) -> None:
        self._run("switch", "--detach", revision)


def replace_live_database(backup_directory: Path, live_database: Path) -> None:
    manifest = parse_manifest(backup_directory / MANIFEST_FILE)
    source = backup_directory / DATABASE_FILE
    live_database.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{live_database.name}.rollback-", dir=live_database.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o600)
        fsync_file(temporary)
        if temporary.stat().st_size != manifest.database_bytes:
            raise RecoveryError("rollback database size does not match manifest")
        if file_sha256(temporary) != manifest.database_sha256:
            raise RecoveryError("rollback database digest does not match manifest")
        inspect_database(temporary)
        temporary.replace(live_database)
        Path(f"{live_database}-wal").unlink(missing_ok=True)
        Path(f"{live_database}-shm").unlink(missing_ok=True)
        live_database.chmod(0o600)
        fsync_directory(live_database.parent)
    finally:
        temporary.unlink(missing_ok=True)


def rollback_live(
    backup_directory: Path,
    *,
    confirmation: str,
    live_database: Path,
    backup_root: Path,
    environment: Mapping[str, str],
    revision_control: RevisionControl,
    service_control: ServiceControl,
    services: Sequence[ServiceDefinition] = SERVICES,
    startup_probe: StartupProbe = default_startup_probe,
    root: Path = ROOT,
) -> BackupManifest:
    manifest = verify_backup(backup_directory, startup_probe=startup_probe, root=root)
    if confirmation != manifest.backup_id:
        raise RecoveryError("rollback confirmation must exactly match the backup identifier")
    revision_control.require_clean()
    original_revision = revision_control.current()
    revision_control.validate_release(manifest.source_revision)
    safety_directory, _safety = create_backup(
        live_database,
        backup_root,
        source_revision=original_revision,
        kind="pre_rollback",
        target_revision=manifest.source_revision,
        environment=environment,
        retain=None,
    )
    try:
        service_control.stop(services)
        revision_control.switch(manifest.source_revision)
        replace_live_database(backup_directory, live_database)
        service_control.install(services)
    except Exception as error:
        try:
            replace_live_database(safety_directory, live_database)
            revision_control.switch(original_revision)
            service_control.install(services)
        except Exception as recovery_error:
            raise RecoveryError(
                "rollback failed and automatic recovery was incomplete"
            ) from recovery_error
        raise RecoveryError(
            "rollback failed; the original revision and database were restored"
        ) from error
    return manifest


def complete_upgrade(
    backup_directory: Path,
    *,
    deployed_revision: str,
    observed_revision: str,
    environment: Mapping[str, str],
) -> Path:
    manifest = parse_manifest(backup_directory / MANIFEST_FILE)
    if manifest.kind != "pre_upgrade" or manifest.target_revision is None:
        raise RecoveryError("backup is not an upgrade preflight")
    if deployed_revision != manifest.target_revision or observed_revision != deployed_revision:
        raise RecoveryError("deployed revision does not match the upgrade preflight")
    receipt = backup_directory / "deployment.json"
    if receipt.exists():
        raise RecoveryError("upgrade receipt already exists")
    payload: dict[str, object] = {
        "schema": DEPLOYMENT_SCHEMA,
        "backup_id": manifest.backup_id,
        "deployed_at": datetime.now(UTC).isoformat(),
        "deployed_revision": deployed_revision,
    }
    write_private(receipt, serialize_json(payload, environment))
    return receipt


def command_output(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RecoveryError("required command failed") from error
    return completed.stdout.strip()


def resolve_backup(backup_root: Path, backup_id: str) -> Path:
    if not backup_id.startswith("bkp_") or Path(backup_id).name != backup_id:
        raise RecoveryError("backup identifier is invalid")
    path = (backup_root / backup_id).resolve()
    if path.parent != backup_root.resolve():
        raise RecoveryError("backup identifier escapes the backup root")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Back up and recover Radcliffe SQLite state")
    result.add_argument(
        "--database",
        type=Path,
        default=Path(os.environ.get("RADCLIFFE_DATABASE_PATH", "var/radcliffe.sqlite3")),
    )
    result.add_argument(
        "--backup-root",
        type=Path,
        default=Path(os.environ.get("RADCLIFFE_BACKUP_ROOT", "var/backups")),
    )
    commands = result.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create and verify a consistent backup")
    backup.add_argument("--retain", type=int, default=7)
    backup.add_argument("--kind", choices=("manual", "drill"), default="manual")
    pre_upgrade = commands.add_parser("pre-upgrade", help="back up before deploying a revision")
    pre_upgrade.add_argument("--target-revision", required=True)
    pre_upgrade.add_argument("--retain", type=int, default=7)
    verify = commands.add_parser("verify", help="verify a backup in an isolated app startup")
    verify.add_argument("backup_id")
    restore = commands.add_parser("restore", help="restore only to a non-live destination")
    restore.add_argument("backup_id")
    restore.add_argument("--destination", type=Path, required=True)
    prune = commands.add_parser("prune", help="show or apply bounded retention")
    prune.add_argument("--retain", type=int, required=True)
    prune.add_argument("--apply", action="store_true")
    complete = commands.add_parser("complete-upgrade", help="record a successful deployment")
    complete.add_argument("backup_id")
    complete.add_argument("--deployed-revision", required=True)
    rollback = commands.add_parser("rollback", help="restore live state and its recorded revision")
    rollback.add_argument("backup_id")
    rollback.add_argument("--confirm", required=True)
    return result


def absolute(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def main() -> int:
    arguments = parser().parse_args()
    database = absolute(ROOT, arguments.database)
    backup_root = absolute(ROOT, arguments.backup_root)
    revision = command_output("git", "rev-parse", "HEAD")
    if arguments.command in {"backup", "pre-upgrade"}:
        kind = arguments.kind if arguments.command == "backup" else "pre_upgrade"
        target = arguments.target_revision if arguments.command == "pre-upgrade" else None
        if target is not None:
            GitRevisionControl(ROOT).validate_release(target)
            target = command_output("git", "rev-parse", f"{target}^{{commit}}")
        directory, manifest = create_backup(
            database,
            backup_root,
            source_revision=revision,
            kind=kind,
            target_revision=target,
            retain=arguments.retain,
        )
        verify_backup(directory)
        print(
            serialize_json({"status": "verified", "backup_id": manifest.backup_id}, os.environ),
            end="",
        )
        return 0
    if arguments.command == "prune":
        stale = prune_backups(backup_root, retain=arguments.retain, apply=arguments.apply)
        print(
            serialize_json(
                {"status": "applied" if arguments.apply else "dry_run", "remove": stale},
                os.environ,
            ),
            end="",
        )
        return 0
    directory = resolve_backup(backup_root, arguments.backup_id)
    if arguments.command == "verify":
        manifest = verify_backup(directory)
        print(
            serialize_json({"status": "verified", "backup_id": manifest.backup_id}, os.environ),
            end="",
        )
        return 0
    if arguments.command == "restore":
        manifest = restore_backup(
            directory,
            absolute(ROOT, arguments.destination),
            live_database=database,
        )
        print(
            serialize_json({"status": "restored", "backup_id": manifest.backup_id}, os.environ),
            end="",
        )
        return 0
    if arguments.command == "complete-upgrade":
        complete_upgrade(
            directory,
            deployed_revision=arguments.deployed_revision,
            observed_revision=revision,
            environment=os.environ,
        )
        print(
            serialize_json({"status": "recorded", "backup_id": arguments.backup_id}, os.environ),
            end="",
        )
        return 0
    if arguments.command == "rollback":
        if sys.platform != "darwin":
            raise RecoveryError("live rollback requires the macOS service profile")
        manager = ServiceManager(
            root=ROOT,
            launch_agents=Path.home() / "Library/LaunchAgents",
            uid=os.getuid(),
        )
        manifest = rollback_live(
            directory,
            confirmation=arguments.confirm,
            live_database=database,
            backup_root=backup_root,
            environment=os.environ,
            revision_control=GitRevisionControl(ROOT),
            service_control=manager,
        )
        print(
            serialize_json({"status": "rolled_back", "backup_id": manifest.backup_id}, os.environ),
            end="",
        )
        return 0
    raise RecoveryError("unknown recovery command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryError as error:
        print(f"recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
