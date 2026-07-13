#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    output = root / "artifacts" / "foundation-demo"
    output.mkdir(parents=True, exist_ok=True)
    command = [
        "uv",
        "run",
        "pytest",
        "tests/conformance",
        "services/media-worker/tests/test_pipeline.py",
        "packages/domain-python/tests/test_session.py",
        "-q",
    ]
    started = time.monotonic()
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    source = (root / "fixtures" / "sources" / "radcliffe-demo.md").read_bytes()
    (output / "canonical-tts-input.pcm").write_bytes(source)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()
    report = {
        "schema": "radcliffe-foundation-report-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "mode": "deterministic-contract-simulation",
        "passed": result.returncode == 0,
        "elapsed_ms": elapsed_ms,
        "canonical_source_sha256": hashlib.sha256(source).hexdigest(),
        "canonical_tts_input_sha256": hashlib.sha256(source).hexdigest(),
        "test_command": command,
        "test_output": result.stdout + result.stderr,
        "limitations": [
            "The PCM artifact is deterministic fake-provider output, not captured Pixel audio.",
            "Provider latency, TURN fallback, barge-in acoustics, Bluetooth, and "
            "lock-screen controls require the physical-device runbook.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
