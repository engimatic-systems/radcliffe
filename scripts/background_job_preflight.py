#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from radcliffe_control.job_executor import JobExecutorSupervisor, JsonLineJobLauncher
from radcliffe_domain.catalog import SessionCatalog
from radcliffe_domain.jobs import JobStatus, JobStore
from radcliffe_domain.store import EventStore

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_CONTROL_CAPABILITIES = frozenset({"background_jobs_v1", "promoted_job_results_v1"})
REQUIRED_SIDECAR_TOOLS = frozenset({"run_job", "manage_job"})
REQUIRED_USER_TEST_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True, slots=True)
class Check:
    component: str
    status: str
    code: str


def fetch_json(url: str) -> tuple[int | None, object | None]:
    try:
        with urlopen(  # noqa: S310 - endpoints are fixed by the protected local profile
            Request(url, headers={"accept": "application/json"}), timeout=3
        ) as response:
            return response.status, json.loads(response.read(256_000))
    except HTTPError as error:
        try:
            return error.code, json.loads(error.read(256_000))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return error.code, None
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None, None


def deployment_check(
    component: str,
    payload: object,
    *,
    expected_revision: str,
    required_capabilities: frozenset[str],
    nested_tools: bool = False,
) -> Check:
    body = payload if isinstance(payload, dict) else {}
    if body.get("status") != "ok":
        return Check(component, "failed", f"{component}_unavailable")
    if body.get("build_revision") != expected_revision:
        return Check(component, "failed", f"{component}_revision_mismatch")
    raw_capabilities = body.get("capabilities")
    if nested_tools:
        raw_capabilities = (
            raw_capabilities.get("tools") if isinstance(raw_capabilities, dict) else None
        )
    capabilities = (
        {item for item in raw_capabilities if isinstance(item, str)}
        if isinstance(raw_capabilities, list)
        else set()
    )
    if not required_capabilities.issubset(capabilities):
        return Check(component, "failed", f"{component}_capability_missing")
    return Check(component, "ok", f"{component}_deployed")


def executor_check(payload: object) -> Check:
    body = payload if isinstance(payload, dict) else {}
    raw_checks = body.get("checks")
    checks = raw_checks if isinstance(raw_checks, list) else []
    executor = next(
        (
            item
            for item in checks
            if isinstance(item, dict) and item.get("component") == "job_executor"
        ),
        None,
    )
    if (
        body.get("status") == "ready"
        and isinstance(executor, dict)
        and executor.get("status") == "ok"
        and executor.get("code") == "job_executor_running"
    ):
        return Check("job_executor", "ok", "job_executor_running")
    if isinstance(executor, dict) and executor.get("code") == "job_executor_disabled":
        return Check("job_executor", "failed", "job_executor_disabled")
    return Check("job_executor", "failed", "job_executor_unavailable")


def foreground_model_check(payload: object) -> Check:
    body = payload if isinstance(payload, dict) else {}
    ready = (
        body.get("provider") == "anthropic"
        and body.get("model_id") == REQUIRED_USER_TEST_MODEL
        and body.get("data_boundary") == "cloud_anthropic"
    )
    return Check(
        "foreground_model",
        "ok" if ready else "failed",
        "haiku_user_test_profile" if ready else "foreground_model_not_haiku",
    )


def job_model_ready(environment: Mapping[str, str]) -> bool:
    try:
        command = shlex.split(environment.get("RADCLIFFE_JOB_EXECUTOR_COMMAND", ""))
        provider_index = command.index("--provider")
        model_index = command.index("--model")
        image_index = command.index("--image")
        configured_image = environment.get("RADCLIFFE_JOB_SANDBOX_IMAGE", "")
        command_ready = (
            command[provider_index + 1] == "anthropic"
            and command[model_index + 1] == REQUIRED_USER_TEST_MODEL
            and bool(configured_image)
            and command[image_index + 1] == configured_image
        )
    except (ValueError, IndexError):
        command_ready = False
    return (
        environment.get("RADCLIFFE_JOB_MODEL_PROVIDER") == "anthropic"
        and environment.get("RADCLIFFE_JOB_ALLOW_CLOUD_MODELS") == "1"
        and bool(environment.get("RADCLIFFE_ANTHROPIC_API_KEY"))
        and command_ready
    )


def background_model_check(payload: object, configuration_ready: bool) -> Check:
    body = payload if isinstance(payload, dict) else {}
    ready = (
        configuration_ready
        and body.get("background_model_provider") == "anthropic"
        and body.get("background_model_id") == REQUIRED_USER_TEST_MODEL
    )
    return Check(
        "background_model",
        "ok" if ready else "failed",
        "haiku_job_profile" if ready else "background_model_not_haiku",
    )


def validate_live(
    *,
    expected_revision: str,
    control_health: object,
    control_readiness: object,
    sidecar_health: object,
    image_available: bool,
    synthetic_smoke: bool,
    job_model_ready: bool,
) -> tuple[Check, ...]:
    return (
        deployment_check(
            "control",
            control_health,
            expected_revision=expected_revision,
            required_capabilities=REQUIRED_CONTROL_CAPABILITIES,
        ),
        deployment_check(
            "sidecar",
            sidecar_health,
            expected_revision=expected_revision,
            required_capabilities=REQUIRED_SIDECAR_TOOLS,
            nested_tools=True,
        ),
        foreground_model_check(sidecar_health),
        executor_check(control_readiness),
        background_model_check(control_health, job_model_ready),
        Check(
            "sandbox_image",
            "ok" if image_available else "failed",
            "sandbox_image_available" if image_available else "sandbox_image_unavailable",
        ),
        Check(
            "synthetic_job",
            "ok" if synthetic_smoke else "failed",
            "synthetic_job_completed" if synthetic_smoke else "synthetic_job_failed",
        ),
    )


async def synthetic_executor_smoke() -> bool:
    with tempfile.TemporaryDirectory(prefix="radcliffe-job-preflight-") as temporary:
        root = Path(temporary)
        database = root / "preflight.sqlite3"
        await EventStore(database).migrate()
        session, _created = await SessionCatalog(database).create(
            principal_id="preflight-owner",
            session_id="preflight-session",
        )
        store = JobStore(database)
        launcher = JsonLineJobLauncher(
            command=(sys.executable, str(ROOT / "scripts/synthetic-job-executor.py")),
            workspace_root=root / "jobs",
            max_output_bytes=64 * 1024,
            base_environment={"PATH": os.environ.get("PATH", "")},
        )
        supervisor = JobExecutorSupervisor(
            store=store,
            launcher=launcher,
            poll_seconds=0.01,
            heartbeat_seconds=0.05,
            lease_seconds=2,
            max_concurrency=1,
        )
        accepted = await store.accept(
            principal_id="preflight-owner",
            session_id=session.session_id,
            generation=1,
            idempotency_key="synthetic-preflight-accept",
            instruction="credential-free synthetic deployment check",
        )
        supervisor.start()
        deadline = time.monotonic() + 3
        succeeded = False
        try:
            while time.monotonic() < deadline:
                record = await store.get(
                    principal_id="preflight-owner",
                    job_id=accepted.record.job_id,
                )
                if record is not None and record.status is JobStatus.SUCCEEDED:
                    succeeded = True
                    break
                if record is not None and record.status in {
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }:
                    break
                await asyncio.sleep(0.02)
        finally:
            await supervisor.close()
        return succeeded


def image_available(environment: Mapping[str, str]) -> bool:
    docker = environment.get("RADCLIFFE_JOB_DOCKER_BIN", "")
    docker_host = environment.get("RADCLIFFE_JOB_DOCKER_HOST", "")
    image = environment.get("RADCLIFFE_JOB_SANDBOX_IMAGE", "")
    if not docker or not docker_host.startswith("unix:///") or not image:
        return False
    try:
        result = subprocess.run(
            [docker, "-H", docker_host, "image", "inspect", image],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Gate background-job phone testing")
    value.add_argument("--control-url", default="http://127.0.0.1:8000")
    value.add_argument("--sidecar-url", default="http://127.0.0.1:8787")
    value.add_argument("--wait", type=float, default=0)
    return value


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    expected = git_revision()
    deadline = time.monotonic() + max(0, options.wait)
    while True:
        _control_status, control_health = fetch_json(f"{options.control_url.rstrip('/')}/healthz")
        _ready_status, control_readiness = fetch_json(f"{options.control_url.rstrip('/')}/readyz")
        _sidecar_status, sidecar_health = fetch_json(f"{options.sidecar_url.rstrip('/')}/healthz")
        deployment = validate_live(
            expected_revision=expected,
            control_health=control_health,
            control_readiness=control_readiness,
            sidecar_health=sidecar_health,
            image_available=True,
            synthetic_smoke=True,
            job_model_ready=job_model_ready(os.environ),
        )
        if all(check.status == "ok" for check in deployment[:5]):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    checks = validate_live(
        expected_revision=expected,
        control_health=control_health,
        control_readiness=control_readiness,
        sidecar_health=sidecar_health,
        image_available=image_available(os.environ),
        synthetic_smoke=asyncio.run(synthetic_executor_smoke()),
        job_model_ready=job_model_ready(os.environ),
    )
    report = {
        "schema": "radcliffe-background-job-preflight-v1",
        "status": "ready" if all(check.status == "ok" for check in checks) else "not_ready",
        "revision": expected,
        "checks": [asdict(check) for check in checks],
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
