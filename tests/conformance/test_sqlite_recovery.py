import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from radcliffe_domain.store import EventStore

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = spec_from_file_location("radcliffe_recovery_under_test", SCRIPTS / "radcliffe_recovery.py")
assert SPEC is not None and SPEC.loader is not None
RECOVERY = module_from_spec(SPEC)
sys.modules[SPEC.name] = RECOVERY
SPEC.loader.exec_module(RECOVERY)
ROOT = Path(__file__).resolve().parents[2]


def populated_database(path: Path, *, suffix: str = "one") -> Path:
    asyncio.run(EventStore(path).migrate())
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as database:
        database.execute(
            """INSERT INTO sessions(
                session_id, principal_id, idempotency_key, created_at, last_activity_at,
                title, status, protocol_version, generation, worker_mode, worker_status,
                worker_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', 2, 1, 'managed', 'stopped', ?)""",
            (
                f"session-{suffix}",
                "owner",
                f"create-{suffix}",
                now,
                now,
                f"Private conversation {suffix}",
                now,
            ),
        )
        database.execute(
            "INSERT INTO envelopes VALUES (?, 1, ?, ?, ?, ?)",
            (
                f"session-{suffix}",
                f"message-{suffix}",
                b"content-digest",
                f"private message body {suffix}".encode(),
                now,
            ),
        )
        database.execute(
            """INSERT INTO playback_acknowledgements VALUES (
                ?, ?, 1, ?, ?, 'completed', 900, 3, ?, '', ?
            )""",
            (
                f"ack-{suffix}",
                f"session-{suffix}",
                f"operation-{suffix}",
                f"segment-{suffix}",
                now,
                f"playback-{suffix}",
            ),
        )
        database.execute(
            """INSERT INTO tool_calls VALUES (
                ?, ?, ?, 1, 'read_text_source', ?, 'grant', 'allow', 'succeeded',
                '/private/source/path', ?, ?, ?, ?
            )""",
            (
                f"call-{suffix}",
                f"tool-key-{suffix}",
                f"session-{suffix}",
                b'{"path":"private.txt"}',
                b"source-digest",
                b'{"text":"private source text"}',
                now,
                now,
            ),
        )
        database.execute(
            """INSERT INTO devices VALUES (
                ?, 'owner', ?, ?, 'Pixel', ?, ?, NULL, NULL
            )""",
            (
                f"device-{suffix}",
                f"installation-{suffix}",
                f"participant-{suffix}",
                f"credential-digest-{suffix}".encode(),
                now,
            ),
        )
        database.execute(
            "INSERT INTO device_auth_audit VALUES (?, 'owner', ?, 'enrolled', 'success', ?)",
            (f"event-{suffix}", f"device-{suffix}", now),
        )
        database.execute(
            """INSERT INTO configuration_values VALUES (
                'owner', 'response_detail', 'session', ?, 'brief', 1, ?, ?
            )""",
            (f"session-{suffix}", f"device-{suffix}", now),
        )
        database.execute(
            "INSERT INTO configuration_mutations VALUES (?, ?, ?, ?, ?)",
            (
                f"configuration-{suffix}",
                f"configuration-key-{suffix}",
                b"configuration-request-digest",
                b'{"value":"brief"}',
                now,
            ),
        )
        database.commit()
    path.chmod(0o600)
    return path


def table_snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(path) as database:
        names = {
            str(row[0])
            for row in database.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
        }
        return {
            table: database.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in RECOVERY.DATA_TABLES
            if table in names
        }


def no_op_startup(_path: Path) -> None:
    return None


def create(
    database: Path,
    backup_root: Path,
    *,
    kind: str = "manual",
    target_revision: str | None = None,
    retain: int | None = 7,
):
    return RECOVERY.create_backup(
        database,
        backup_root,
        source_revision="a" * 40,
        kind=kind,
        target_revision=target_revision,
        environment={
            "RADCLIFFE_INTERNAL_TOKEN": "configured-secret-value",
            "RADCLIFFE_OPENAI_API_KEY": "paid-provider-secret",
        },
        retain=retain,
    )


def test_consistent_private_backup_verifies_current_app_and_restores_all_state(tmp_path) -> None:
    source = populated_database(tmp_path / "live.sqlite3")
    expected = table_snapshot(source)
    backup_root = tmp_path / "backups"

    directory, manifest = create(source, backup_root)
    verified = RECOVERY.verify_backup(directory, root=ROOT)
    restored = tmp_path / "isolated/restored.sqlite3"
    RECOVERY.restore_backup(
        directory,
        restored,
        live_database=source,
        root=ROOT,
    )

    assert verified == manifest
    assert table_snapshot(restored) == expected
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / RECOVERY.DATABASE_FILE).stat().st_mode & 0o777 == 0o600
    assert (directory / RECOVERY.MANIFEST_FILE).stat().st_mode & 0o777 == 0o600
    encoded = (directory / RECOVERY.MANIFEST_FILE).read_text()
    assert "configured-secret-value" not in encoded
    assert "paid-provider-secret" not in encoded
    assert "Private conversation" not in encoded
    assert "/private/source/path" not in encoded
    assert str(source) not in encoded
    assert manifest.inventory.table_rows == {
        "schema_migrations": 7,
        "sessions": 1,
        "envelopes": 1,
        "tool_calls": 1,
        "playback_acknowledgements": 1,
        "devices": 1,
        "device_auth_audit": 1,
        "configuration_values": 1,
        "configuration_mutations": 1,
    }
    with pytest.raises(RECOVERY.RecoveryError, match="cannot replace the live"):
        RECOVERY.restore_backup(
            directory,
            source,
            live_database=source,
            startup_probe=no_op_startup,
            root=ROOT,
        )


def test_corruption_and_migration_failure_never_replace_destination(tmp_path) -> None:
    source = populated_database(tmp_path / "live.sqlite3")
    directory, _manifest = create(source, tmp_path / "backups")
    destination = tmp_path / "restore.sqlite3"
    destination.write_bytes(b"unchanged destination")

    def migration_failure(_path: Path) -> None:
        raise RECOVERY.RecoveryError("synthetic migration failure")

    with pytest.raises(RECOVERY.RecoveryError, match="synthetic migration failure"):
        RECOVERY.restore_backup(
            directory,
            destination,
            live_database=source,
            startup_probe=migration_failure,
            root=ROOT,
        )
    assert destination.read_bytes() == b"unchanged destination"

    database = directory / RECOVERY.DATABASE_FILE
    database.write_bytes(database.read_bytes()[:128] + b"corruption")
    with pytest.raises(RECOVERY.RecoveryError, match="does not match manifest"):
        RECOVERY.verify_backup(directory, startup_probe=no_op_startup, root=ROOT)
    assert destination.read_bytes() == b"unchanged destination"


def test_insufficient_disk_interruption_and_partial_restore_are_atomic(tmp_path) -> None:
    source = populated_database(tmp_path / "live.sqlite3")
    backup_root = tmp_path / "backups"

    with pytest.raises(RECOVERY.RecoveryError, match="insufficient disk"):
        RECOVERY.create_backup(
            source,
            backup_root,
            source_revision="a" * 40,
            disk_free=lambda _path: 0,
        )

    def interrupt(_status: int, _remaining: int, _total: int) -> None:
        raise RECOVERY.RecoveryError("synthetic interrupted backup")

    with pytest.raises(RECOVERY.RecoveryError, match="interrupted backup"):
        RECOVERY.create_backup(
            source,
            backup_root,
            source_revision="a" * 40,
            progress=interrupt,
        )
    assert not tuple(backup_root.glob(".pending-*"))
    assert not RECOVERY.backup_directories(backup_root)

    directory, _manifest = create(source, backup_root)
    destination = tmp_path / "restore.sqlite3"
    destination.write_bytes(b"original destination")

    def partial_copy(source_path: Path, destination_path: Path) -> object:
        destination_path.write_bytes(source_path.read_bytes()[:64])
        raise OSError("synthetic interrupted copy")

    with pytest.raises(RECOVERY.RecoveryError, match="restore copy failed"):
        RECOVERY.restore_backup(
            directory,
            destination,
            live_database=source,
            startup_probe=no_op_startup,
            root=ROOT,
            copy_file=partial_copy,
        )
    assert destination.read_bytes() == b"original destination"


def test_retention_is_bounded_with_explicit_dry_run_and_apply(tmp_path) -> None:
    source = populated_database(tmp_path / "live.sqlite3")
    backup_root = tmp_path / "backups"
    created = [create(source, backup_root, retain=None)[1].backup_id for _ in range(4)]
    invalid = backup_root / "bkp_invalid"
    invalid.mkdir()
    (invalid / RECOVERY.MANIFEST_FILE).write_text("not json")

    dry_run = RECOVERY.prune_backups(backup_root, retain=2, apply=False)
    assert len(dry_run) == 2
    assert all((backup_root / backup_id).is_dir() for backup_id in created)
    assert invalid.is_dir()

    removed = RECOVERY.prune_backups(backup_root, retain=2, apply=True)
    assert removed == dry_run
    assert len(RECOVERY.backup_directories(backup_root)) == 2
    assert invalid.is_dir()


def test_upgrade_receipt_requires_the_preflight_target_and_contains_no_secrets(tmp_path) -> None:
    source = populated_database(tmp_path / "live.sqlite3")
    directory, manifest = create(
        source,
        tmp_path / "backups",
        kind="pre_upgrade",
        target_revision="b" * 40,
    )
    environment = {"RADCLIFFE_INTERNAL_TOKEN": "receipt-secret-value"}

    with pytest.raises(RECOVERY.RecoveryError, match="does not match"):
        RECOVERY.complete_upgrade(
            directory,
            deployed_revision="c" * 40,
            observed_revision="c" * 40,
            environment=environment,
        )
    receipt = RECOVERY.complete_upgrade(
        directory,
        deployed_revision="b" * 40,
        observed_revision="b" * 40,
        environment=environment,
    )

    payload = json.loads(receipt.read_text())
    assert payload["backup_id"] == manifest.backup_id
    assert payload["deployed_revision"] == "b" * 40
    assert "receipt-secret-value" not in receipt.read_text()
    assert receipt.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RECOVERY.RecoveryError, match="configured secret"):
        RECOVERY.serialize_json({"leaked": "receipt-secret-value"}, environment)


class FakeRevisionControl:
    def __init__(self) -> None:
        self.revision = "current-revision"
        self.switches: list[str] = []
        self.validated: list[str] = []
        self.clean_checks = 0

    def current(self) -> str:
        return self.revision

    def require_clean(self) -> None:
        self.clean_checks += 1

    def validate_release(self, revision: str) -> None:
        self.validated.append(revision)

    def switch(self, revision: str) -> None:
        self.revision = revision
        self.switches.append(revision)


class FakeServices:
    def __init__(self, *, fail_first_install: bool = False) -> None:
        self.events: list[str] = []
        self.fail_first_install = fail_first_install

    def stop(self, _services) -> None:
        self.events.append("stop")

    def install(self, _services) -> None:
        self.events.append("install")
        if self.fail_first_install:
            self.fail_first_install = False
            raise RuntimeError("synthetic service failure")


def test_confirmed_rollback_switches_revision_and_restores_recorded_database(tmp_path) -> None:
    live = populated_database(tmp_path / "live.sqlite3", suffix="before")
    backup_root = tmp_path / "backups"
    directory, manifest = create(live, backup_root, retain=None)
    populated_database(live, suffix="after")
    revision = FakeRevisionControl()
    services = FakeServices()

    with pytest.raises(RECOVERY.RecoveryError, match="confirmation"):
        RECOVERY.rollback_live(
            directory,
            confirmation="wrong-backup",
            live_database=live,
            backup_root=backup_root,
            environment={},
            revision_control=revision,
            service_control=services,
            startup_probe=no_op_startup,
            root=ROOT,
        )
    assert not services.events

    RECOVERY.rollback_live(
        directory,
        confirmation=manifest.backup_id,
        live_database=live,
        backup_root=backup_root,
        environment={},
        revision_control=revision,
        service_control=services,
        startup_probe=no_op_startup,
        root=ROOT,
    )

    assert revision.switches == ["a" * 40]
    assert revision.validated == ["a" * 40]
    assert services.events == ["stop", "install"]
    assert not Path(f"{live}-wal").exists()
    assert not Path(f"{live}-shm").exists()
    assert RECOVERY.inspect_database(live).table_rows["sessions"] == 1
    assert len(RECOVERY.backup_directories(backup_root)) == 2


def test_failed_rollback_restores_original_database_revision_and_services(tmp_path) -> None:
    live = populated_database(tmp_path / "live.sqlite3", suffix="backup")
    backup_root = tmp_path / "backups"
    directory, manifest = create(live, backup_root, retain=None)
    populated_database(live, suffix="current")
    expected = table_snapshot(live)
    revision = FakeRevisionControl()
    services = FakeServices(fail_first_install=True)

    with pytest.raises(RECOVERY.RecoveryError, match="original revision and database"):
        RECOVERY.rollback_live(
            directory,
            confirmation=manifest.backup_id,
            live_database=live,
            backup_root=backup_root,
            environment={},
            revision_control=revision,
            service_control=services,
            startup_probe=no_op_startup,
            root=ROOT,
        )

    assert table_snapshot(live) == expected
    assert revision.revision == "current-revision"
    assert revision.switches == ["a" * 40, "current-revision"]
    assert services.events == ["stop", "install", "install"]
    assert live.stat().st_mode & 0o777 == 0o600
