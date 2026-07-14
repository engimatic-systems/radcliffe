from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from livekit import rtc

ROOT = Path(__file__).resolve().parent.parent


async def wait_until(predicate, limit_seconds: float = 30) -> None:
    async with asyncio.timeout(limit_seconds):
        while not predicate():  # noqa: ASYNC110 - polling external room state
            await asyncio.sleep(0.05)


async def run(commit: str) -> int:
    control_url = os.environ.get("RADCLIFFE_CONTROL_URL", "http://127.0.0.1:8000").rstrip("/")
    bootstrap_token = os.environ.get("RADCLIFFE_BOOTSTRAP_TOKEN")
    if not bootstrap_token:
        raise RuntimeError("RADCLIFFE_BOOTSTRAP_TOKEN is required")
    headers = {"Authorization": f"Bearer {bootstrap_token}"}
    idempotency_key = f"worker-smoke-{uuid4()}"
    created_at = datetime.now(UTC)
    report: dict[str, object] = {
        "schema": "radcliffe-worker-dispatch-conformance-v1",
        "created_at": created_at.isoformat(),
        "commit": commit,
        "checks": [],
    }
    checks: list[dict[str, object]] = report["checks"]  # type: ignore[assignment]
    room = rtc.Room()
    session_id: str | None = None
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            started = time.monotonic()
            response = await client.post(
                f"{control_url}/v1/sessions",
                headers=headers,
                json={
                    "participant_identity": "worker-dispatch-smoke",
                    "idempotency_key": idempotency_key,
                    "title": "Automated worker dispatch verification",
                },
            )
            response.raise_for_status()
            created = response.json()
            session_id = str(created["session_id"])
            checks.append(
                {
                    "name": "server_generated_session",
                    "passed": session_id.startswith("ses_"),
                    "detail": session_id,
                }
            )
            retry = await client.post(
                f"{control_url}/v1/sessions",
                headers=headers,
                json={
                    "participant_identity": "worker-dispatch-smoke-retry",
                    "idempotency_key": idempotency_key,
                },
            )
            retry.raise_for_status()
            retried = retry.json()
            checks.append(
                {
                    "name": "idempotent_dispatch",
                    "passed": retried["session_id"] == session_id,
                    "detail": "creation retry returned the same session",
                }
            )
            await room.connect(created["livekit_url"], created["participant_token"])
            await wait_until(
                lambda: any(
                    identity.startswith("radcliffe-worker-")
                    for identity in room.remote_participants
                )
            )
            join_seconds = time.monotonic() - started
            workers = tuple(
                identity
                for identity in room.remote_participants
                if identity.startswith("radcliffe-worker-")
            )
            checks.append(
                {
                    "name": "exactly_one_managed_worker_joins",
                    "passed": len(workers) == 1,
                    "detail": f"{len(workers)} worker(s) after {join_seconds:.3f}s",
                    "measured": {"join_seconds": round(join_seconds, 3)},
                }
            )
            detail = await client.get(f"{control_url}/v1/sessions/{session_id}", headers=headers)
            detail.raise_for_status()
            metadata = detail.json()
            checks.append(
                {
                    "name": "worker_status_visible_without_secrets",
                    "passed": metadata["worker_status"] == "running"
                    and "participant_token" not in metadata,
                    "detail": f"worker status {metadata['worker_status']}",
                }
            )
            ended = await client.patch(
                f"{control_url}/v1/sessions/{session_id}",
                headers=headers,
                json={
                    "title": "Automated worker dispatch verification",
                    "status": "ended",
                },
            )
            ended.raise_for_status()
            await wait_until(
                lambda: (
                    not any(
                        identity.startswith("radcliffe-worker-")
                        for identity in room.remote_participants
                    )
                )
            )
            checks.append(
                {
                    "name": "end_cleans_up_worker",
                    "passed": ended.json()["worker_status"] == "stopped",
                    "detail": "worker left the room and durable status is stopped",
                }
            )
        finally:
            await room.disconnect()
            if session_id is not None:
                await client.patch(
                    f"{control_url}/v1/sessions/{session_id}",
                    headers=headers,
                    json={"status": "archived"},
                )
    report["session_id"] = session_id
    report["passed"] = all(bool(check["passed"]) for check in checks)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "artifacts" / "worker-dispatch" / stamp
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(report_path), **report}, indent=2))
    return 0 if report["passed"] else 1


def main() -> int:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return asyncio.run(run(commit))


if __name__ == "__main__":
    raise SystemExit(main())
