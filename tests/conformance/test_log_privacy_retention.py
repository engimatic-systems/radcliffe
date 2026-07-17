import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = spec_from_file_location("radcliffe_logs_under_test", SCRIPTS / "radcliffe_logs.py")
assert SPEC is not None and SPEC.loader is not None
LOGS = module_from_spec(SPEC)
sys.modules[SPEC.name] = LOGS
SPEC.loader.exec_module(LOGS)


def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "radcliffe"
    (root / "var/workers").mkdir(parents=True)
    (root / "var/telemetry").mkdir()
    (root / "artifacts/diagnostics").mkdir(parents=True)
    for relative in LOGS.STATIC_LOGS.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content-free phase=ready duration_ms=12\n")
        path.chmod(0o600)
    worker = root / "var/workers/session.log"
    worker.write_text("content-free worker lifecycle\n")
    worker.chmod(0o600)
    return root


def test_audit_rejects_credentials_private_content_audio_and_insecure_modes(tmp_path) -> None:
    root = workspace(tmp_path)
    files = LOGS.discover(root)
    assert not LOGS.scan(files, forbidden_values=(b"private message canary",))

    control = root / "var/control-plane.log"
    control.write_bytes(
        b"Authorization: Bearer bootstrap-secret-value\n"
        b"rdc_dev_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG\n"
        b"sk-ant-abcdefghijklmnopqrstuvwxyz\n"
        b"private message canary\n"
        b"\x00\x01raw pcm"
    )
    control.chmod(0o644)
    violations = LOGS.scan(LOGS.discover(root), forbidden_values=(b"private message canary",))
    codes = {item.code for item in violations}

    assert codes == {
        "authorization_value",
        "device_credential",
        "provider_credential",
        "private_content_or_secret",
        "binary_or_microphone_audio",
        "insecure_mode",
    }
    assert {item.logical_name for item in violations} == {"control"}


class FakeManager:
    def __init__(self) -> None:
        self.events: list[str] = []

    def stop(self, _services) -> None:
        self.events.append("stop")

    def start(self, _services) -> None:
        self.events.append("start")


def test_stop_safe_rotation_prunes_and_enforces_owner_only_permissions(tmp_path) -> None:
    root = workspace(tmp_path)
    for item in LOGS.discover(root):
        item.path.write_text("x" * 200)
        item.path.chmod(0o644)
    stale = root / "var/workers/stale.log"
    stale.write_text("old")
    old = 1_600_000_000
    os.utime(stale, (old, old))
    manager = FakeManager()

    result = LOGS.maintain(
        root,
        maximum_bytes=100,
        keep=2,
        maximum_age_days=14,
        manager=manager,
    )

    assert manager.events == ["stop", "start"]
    assert result["rotated"] == 5
    assert result["removed"] == 1
    assert result["purged"] == 0
    assert not stale.exists()
    assert all(item.path.stat().st_mode & 0o777 == 0o600 for item in LOGS.discover(root))
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.rglob("*.1"))
    assert (root / "var").stat().st_mode & 0o777 == 0o700


def test_maintenance_purges_private_current_and_rotated_logs(tmp_path) -> None:
    root = workspace(tmp_path)
    control = root / "var/control-plane.log"
    rotation = control.with_name("control-plane.log.1")
    private_value = b"private conversation canary"
    control.write_bytes(private_value)
    rotation.write_bytes(private_value)
    rotation.chmod(0o600)

    result = LOGS.maintain(
        root,
        maximum_bytes=1_000,
        keep=2,
        maximum_age_days=14,
        manager=None,
        forbidden_values=(private_value,),
    )

    assert result["purged"] == 2
    assert not control.exists()
    assert not rotation.exists()
    assert not LOGS.scan(
        LOGS.discover(root, include_rotated=True),
        forbidden_values=(private_value,),
    )


def test_orphan_rotations_are_discovered_pruned_and_privacy_index_fails_closed(tmp_path) -> None:
    root = workspace(tmp_path)
    control = root / "var/control-plane.log"
    control.unlink()
    orphan = control.with_name("control-plane.log.8")
    orphan.write_text("content-free orphan rotation")
    orphan.chmod(0o600)

    discovered = LOGS.discover(root, include_rotated=True)
    assert orphan in {item.path for item in discovered}
    assert LOGS.prune(root, keep=2, maximum_age_days=14) == 1
    assert not orphan.exists()

    database = root / "var/invalid.sqlite3"
    database.write_bytes(b"not a sqlite database")
    with pytest.raises(RuntimeError, match="privacy index"):
        LOGS.private_text_values(database, root / "missing-sources")
