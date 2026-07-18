from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "haiku_paid_smoke.py"
SPEC = importlib.util.spec_from_file_location("haiku_paid_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def test_paid_smoke_requires_both_cloud_opt_in_and_one_run_phrase(monkeypatch) -> None:
    monkeypatch.delenv("RADCLIFFE_ALLOW_CLOUD_MODELS", raising=False)
    monkeypatch.delenv("RADCLIFFE_APPROVE_PAID_SMOKE", raising=False)
    with pytest.raises(SystemExit, match="cloud models"):
        SMOKE.require_explicit_approval()

    monkeypatch.setenv("RADCLIFFE_ALLOW_CLOUD_MODELS", "1")
    with pytest.raises(SystemExit, match="approval phrase"):
        SMOKE.require_explicit_approval()

    monkeypatch.setenv("RADCLIFFE_APPROVE_PAID_SMOKE", SMOKE.APPROVAL_PHRASE)
    SMOKE.require_explicit_approval()


def test_paid_smoke_report_is_owner_only_and_content_free(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report = {
        "schema": "radcliffe-cloud-paid-smoke-v1",
        "provider": "anthropic",
        "model_id": SMOKE.MODEL_ID,
        "usage": {"input_tokens": 20, "output_tokens": 5, "estimated_cost_usd": 0.000045},
        "prompt_or_response_stored": False,
    }

    SMOKE.write_report(report_path, report)

    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert report_path.stat().st_mode & 0o777 == 0o600
    encoded = report_path.read_text(encoding="utf-8")
    assert "Reply with exactly" not in encoded
    assert "Radcliffe cloud smoke passed" not in encoded
