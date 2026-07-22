import asyncio
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SPEC = spec_from_file_location(
    "background_job_preflight_under_test", SCRIPTS / "background_job_preflight.py"
)
assert SPEC is not None and SPEC.loader is not None
PREFLIGHT = module_from_spec(SPEC)
sys.modules[SPEC.name] = PREFLIGHT
SPEC.loader.exec_module(PREFLIGHT)


def healthy_payloads(revision: str):
    return {
        "control_health": {
            "status": "ok",
            "build_revision": revision,
            "capabilities": ["background_jobs_v1", "promoted_job_results_v1"],
            "background_model_provider": "anthropic",
            "background_model_id": "claude-haiku-4-5-20251001",
        },
        "control_readiness": {
            "status": "ready",
            "checks": [
                {
                    "component": "job_executor",
                    "status": "ok",
                    "code": "job_executor_running",
                }
            ],
        },
        "sidecar_health": {
            "status": "ok",
            "build_revision": revision,
            "capabilities": {"tools": ["list_sources", "run_job", "manage_job"]},
            "provider": "anthropic",
            "model_id": "claude-haiku-4-5-20251001",
            "data_boundary": "cloud_anthropic",
        },
        "image_available": True,
        "synthetic_smoke": True,
        "job_model_ready": True,
    }


def codes(checks) -> dict[str, str]:
    return {check.component: check.code for check in checks}


def test_preflight_accepts_matching_deployed_feature() -> None:
    checks = PREFLIGHT.validate_live(expected_revision="a" * 40, **healthy_payloads("a" * 40))
    assert all(check.status == "ok" for check in checks)


def test_preflight_reproduces_stale_sidecar_and_missing_job_tools() -> None:
    payloads = healthy_payloads("a" * 40)
    payloads["sidecar_health"] = {
        "status": "ok",
        "capabilities": {"tools": ["list_sources", "read_source"]},
    }
    checks = PREFLIGHT.validate_live(expected_revision="a" * 40, **payloads)
    assert codes(checks)["sidecar"] == "sidecar_revision_mismatch"

    payloads["sidecar_health"]["build_revision"] = "a" * 40
    checks = PREFLIGHT.validate_live(expected_revision="a" * 40, **payloads)
    assert codes(checks)["sidecar"] == "sidecar_capability_missing"


def test_preflight_rejects_control_revision_mismatch_and_disabled_executor() -> None:
    payloads = healthy_payloads("a" * 40)
    payloads["control_health"]["build_revision"] = "b" * 40
    payloads["control_readiness"] = {
        "status": "ready",
        "checks": [
            {
                "component": "job_executor",
                "status": "disabled",
                "code": "job_executor_disabled",
            }
        ],
    }
    checks = PREFLIGHT.validate_live(expected_revision="a" * 40, **payloads)
    assert codes(checks)["control"] == "control_revision_mismatch"
    assert codes(checks)["job_executor"] == "job_executor_disabled"


def test_preflight_rejects_qwen_in_either_user_test_model_role() -> None:
    payloads = healthy_payloads("a" * 40)
    payloads["sidecar_health"]["provider"] = "ollama"
    payloads["sidecar_health"]["model_id"] = "qwen3:8b"
    payloads["sidecar_health"]["data_boundary"] = "local"
    payloads["job_model_ready"] = False

    checks = PREFLIGHT.validate_live(expected_revision="a" * 40, **payloads)

    assert codes(checks)["foreground_model"] == "foreground_model_not_haiku"
    assert codes(checks)["background_model"] == "background_model_not_haiku"


def test_job_model_readiness_rejects_a_stale_embedded_sandbox_image() -> None:
    environment = {
        "RADCLIFFE_JOB_EXECUTOR_COMMAND": (
            "python -m radcliffe_control.sandbox_agent --provider anthropic "
            "--model claude-haiku-4-5-20251001 --image radcliffe-job-sandbox:current"
        ),
        "RADCLIFFE_JOB_SANDBOX_IMAGE": "radcliffe-job-sandbox:current",
        "RADCLIFFE_JOB_MODEL_PROVIDER": "anthropic",
        "RADCLIFFE_JOB_ALLOW_CLOUD_MODELS": "1",
        "RADCLIFFE_ANTHROPIC_API_KEY": "test-only",
    }
    assert PREFLIGHT.job_model_ready(environment)

    environment["RADCLIFFE_JOB_EXECUTOR_COMMAND"] = environment[
        "RADCLIFFE_JOB_EXECUTOR_COMMAND"
    ].replace(":current", ":stale")
    assert not PREFLIGHT.job_model_ready(environment)


def test_synthetic_preflight_job_uses_the_real_process_contract_without_a_provider() -> None:
    assert asyncio.run(PREFLIGHT.synthetic_executor_smoke())
