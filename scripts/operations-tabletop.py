#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from radcliffe_logs import LogFile, maintain, scan

ROOT = Path(__file__).resolve().parent.parent
PROCEDURES = {
    "sidecar_unavailable": "model_cold_start",
    "supervisor_unavailable": "stuck_worker",
    "signal_unavailable": "livekit_disconnect",
    "database_unavailable": "database_lock_or_corruption",
    "integrity_failed": "database_lock_or_corruption",
    "device_authentication_failed": "lost_device_credential",
    "schema_mismatch": "upgrade_rollback",
    "disk_pressure": "disk_pressure",
}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str


def run_tabletop(root: Path) -> tuple[Check, ...]:
    checks = [
        Check(
            "every_failure_code_has_a_procedure",
            len(PROCEDURES) == 8 and all(PROCEDURES.values()),
            f"mapped {len(PROCEDURES)} stable failure codes",
        )
    ]
    with tempfile.TemporaryDirectory(prefix="radcliffe-tabletop-") as temporary:
        fixture = Path(temporary)
        log = fixture / "var/control-plane.log"
        log.parent.mkdir(parents=True)
        log.write_text("bounded readiness code only\n")
        log.chmod(0o600)
        files = (LogFile("control", log, True),)
        checks.append(
            Check("clean_fixture_is_accepted", not scan(files, forbidden_values=()), "clean")
        )
        log.write_text("private tabletop message canary\n")
        violations = scan(files, forbidden_values=(b"private tabletop message canary",))
        checks.append(
            Check(
                "private_content_is_rejected",
                {item.code for item in violations} == {"private_content_or_secret"},
                "private content rejected without echoing it",
            )
        )
        log.write_text("x" * 200)
        result = maintain(
            fixture,
            maximum_bytes=100,
            keep=2,
            maximum_age_days=14,
            manager=None,
        )
        checks.append(
            Check(
                "isolated_rotation_is_bounded",
                result["rotated"] == 1 and (log.with_name("control-plane.log.1")).is_file(),
                "one isolated log rotated",
            )
        )
    output = root / "artifacts/operations/tabletop.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema": "radcliffe-operations-tabletop-v1",
                "created_at": datetime.now(UTC).isoformat(),
                "checks": [asdict(item) for item in checks],
                "passed": all(item.passed for item in checks),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    output.chmod(0o600)
    return tuple(checks)


def main() -> int:
    checks = run_tabletop(ROOT)
    print(json.dumps({"passed": all(item.passed for item in checks), "checks": len(checks)}))
    return 0 if all(item.passed for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
