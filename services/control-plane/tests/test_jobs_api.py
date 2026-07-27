import asyncio
import base64
import hashlib
import json
import sqlite3
import time

from fastapi.testclient import TestClient
from radcliffe_control.app import create_app
from radcliffe_control.job_results import (
    PromotedArtifact,
    PromotedFinding,
    PromotedJobResult,
    PromotedRelatedLink,
    PromotedSource,
    write_promoted_result,
)
from radcliffe_control.settings import Settings
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

DEVICE_CREDENTIAL = "rdc_dev_" + "j" * 43
DEVICE_AUTHORIZATION = {"Authorization": f"Bearer {DEVICE_CREDENTIAL}"}
INTERNAL_AUTHORIZATION = {"Authorization": "Bearer internal-token-1234"}


class HoldingProcess:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.finished = asyncio.Event()
        self._returncode: int | None = None
        self.terminated = False

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def send(self, frame) -> None:
        self.frames.append(dict(frame))

    async def readline(self) -> bytes:
        await self.finished.wait()
        return b""

    async def wait(self) -> int:
        await self.finished.wait()
        assert self._returncode is not None
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15
        self.finished.set()

    def kill(self) -> None:
        self._returncode = -9
        self.finished.set()


class HoldingLauncher:
    def __init__(self) -> None:
        self.processes: list[HoldingProcess] = []

    async def __call__(self, _claim):
        process = HoldingProcess()
        self.processes.append(process)
        return process


def settings(tmp_path) -> Settings:
    source_root = tmp_path / "sources"
    source_root.mkdir(exist_ok=True)
    return Settings(
        livekit_api_key="test-key",
        livekit_api_secret="test-secret-that-is-definitely-long-enough",
        bootstrap_token="bootstrap-token-123",
        internal_token="internal-token-1234",
        owner_id="test-owner",
        database_path=tmp_path / "events.sqlite3",
        source_root=source_root,
        telemetry_path=tmp_path / "telemetry.jsonl",
        managed_workers_enabled=False,
    )


def enroll_and_create_session(client: TestClient) -> None:
    enrolled = client.post(
        "/v1/devices/enroll",
        headers={"Authorization": "Bearer bootstrap-token-123"},
        json={
            "installation_id": "background-jobs-installation",
            "display_name": "Background jobs Pixel",
            "credential": DEVICE_CREDENTIAL,
        },
    )
    assert enrolled.status_code == 200
    created = client.post(
        "/v1/sessions",
        headers=DEVICE_AUTHORIZATION,
        json={"session_id": "session-1", "participant_identity": "pixel"},
    )
    assert created.status_code == 200


def tool(
    client: TestClient,
    *,
    tool_name: str,
    call_id: str,
    arguments: dict[str, object],
):
    return client.post(
        "/v1/tool-calls",
        headers=INTERNAL_AUTHORIZATION,
        json={
            "sessionId": "session-1",
            "generation": 1,
            "operationId": f"background-operation-{call_id}",
            "cancellationId": f"background-cancellation-{call_id}",
            "traceId": "trc_" + "b" * 32,
            "turnId": f"turn-{call_id}",
            "callId": call_id,
            "idempotencyKey": f"background:{call_id}",
            "userMessage": f"verbatim user turn for {call_id}",
            "toolName": tool_name,
            "arguments": arguments,
        },
    )


def test_run_job_accepts_promptly_replays_and_excludes_private_context(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        started = time.monotonic()
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-1",
            arguments={
                "instruction": "private six-month lab-paper search",
                "notificationPolicy": "next_safe_pause",
                "priority": 1,
                "budgetUsd": 0.5,
            },
        )
        elapsed = time.monotonic() - started
        replay = tool(
            client,
            tool_name="run_job",
            call_id="run-1",
            arguments={
                "instruction": "private six-month lab-paper search",
                "notificationPolicy": "next_safe_pause",
                "priority": 1,
                "budgetUsd": 0.5,
            },
        )

        assert accepted.status_code == 200
        assert elapsed < 1
        body = accepted.json()
        job_id = body["details"]["jobId"]
        assert body["details"]["status"] == "queued"
        assert body["details"]["replayed"] == "false"
        assert replay.json()["details"]["replayed"] == "true"
        assert "private" not in accepted.text
        assert "lab-paper" not in accepted.text

        collision = tool(
            client,
            tool_name="run_job",
            call_id="run-1",
            arguments={"instruction": "different private request"},
        )
        assert collision.status_code == 409

        listed = client.get(
            "/v1/sessions/session-1/jobs",
            headers=DEVICE_AUTHORIZATION,
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["job_id"] == job_id
        assert listed.json()["items"][0]["budget_usd"] == 0.5
        assert listed.json()["items"][0]["max_runtime_seconds"] == 300
        assert "private" not in listed.text
        assert client.get("/v1/sessions/session-1/jobs").status_code == 401


def test_job_commands_executor_lease_and_restart_are_deterministic(tmp_path) -> None:
    config = settings(tmp_path)
    private_instruction = "private primary research instruction"
    private_follow_up = "private follow-up about methods"
    private_live_follow_up = "private follow-up added while running"
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-1",
            arguments={"instruction": private_instruction},
        )
        job_id = accepted.json()["details"]["jobId"]

        status = tool(
            client,
            tool_name="manage_job",
            call_id="status-1",
            arguments={"operation": "status", "jobId": job_id},
        )
        follow_up = tool(
            client,
            tool_name="manage_job",
            call_id="follow-up-1",
            arguments={
                "operation": "follow_up",
                "jobId": job_id,
                "instruction": private_follow_up,
            },
        )
        priority = client.post(
            f"/v1/jobs/{job_id}/commands",
            headers=DEVICE_AUTHORIZATION,
            json={
                "operation": "set_priority",
                "priority": 0,
                "idempotency_key": "android-priority-1",
            },
        )
        priority_replay = client.post(
            f"/v1/jobs/{job_id}/commands",
            headers=DEVICE_AUTHORIZATION,
            json={
                "operation": "set_priority",
                "priority": 0,
                "idempotency_key": "android-priority-1",
            },
        )

        assert status.status_code == 200
        assert follow_up.status_code == 200
        assert private_follow_up not in follow_up.text
        assert priority.status_code == 200
        assert priority.json()["job"]["priority"] == 0
        assert priority_replay.json()["replayed"] is True

        unauthorized_claim = client.post(
            "/v1/internal/jobs/claim", json={"lease_owner": "worker-1"}
        )
        assert unauthorized_claim.status_code == 401
        claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "worker-1", "lease_seconds": 60},
        )
        assert claim.status_code == 200
        assert claim.json()["instruction"] == private_instruction
        assert [item["instruction"] for item in claim.json()["follow_ups"]] == [private_follow_up]
        follow_up_id = claim.json()["follow_ups"][0]["follow_up_id"]
        assert claim.json()["job"]["status"] == "running"
        assert (
            client.post(
                "/v1/internal/jobs/claim",
                headers=INTERNAL_AUTHORIZATION,
                json={"lease_owner": "worker-2"},
            ).json()
            is None
        )

        live_follow_up = client.post(
            f"/v1/jobs/{job_id}/commands",
            headers=DEVICE_AUTHORIZATION,
            json={
                "operation": "follow_up",
                "instruction": private_live_follow_up,
                "idempotency_key": "android-follow-up-running-1",
            },
        )
        assert live_follow_up.status_code == 200
        assert private_live_follow_up not in live_follow_up.text

        heartbeat = client.post(
            f"/v1/internal/jobs/{job_id}/heartbeat",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "worker-1", "lease_seconds": 60},
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["job"]["status"] == "running"
        assert heartbeat.json()["follow_ups"][0]["follow_up_id"] == follow_up_id
        assert [item["instruction"] for item in heartbeat.json()["follow_ups"]] == [
            private_follow_up,
            private_live_follow_up,
        ]
        progress = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "worker-1",
                "kind": "progress",
                "detail_code": "searching",
            },
        )
        assert progress.status_code == 200
        assert private_instruction not in progress.text

        cancelled = tool(
            client,
            tool_name="manage_job",
            call_id="cancel-1",
            arguments={"operation": "cancel", "jobId": job_id},
        )
        assert cancelled.status_code == 200
        assert '"cancelRequested":true' in cancelled.json()["modelContent"]
        late_success = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "worker-1",
                "kind": "succeeded",
                "detail_code": "completed",
            },
        )
        assert late_success.status_code == 409
        acknowledged = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "worker-1",
                "kind": "cancelled",
                "detail_code": "cancelled_by_user",
            },
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["status"] == "cancelled"
        events = client.get(
            f"/v1/jobs/{job_id}/events",
            headers=DEVICE_AUTHORIZATION,
        )
        assert events.status_code == 200
        assert [item["kind"] for item in events.json()["items"]] == [
            "accepted",
            "follow_up_added",
            "priority_changed",
            "started",
            "follow_up_added",
            "progress",
            "cancellation_requested",
            "cancelled",
        ]
        assert [item["delivery_sequence"] for item in events.json()["items"]] == list(range(1, 9))
        assert private_instruction not in events.text
        assert private_follow_up not in events.text
        assert private_live_follow_up not in events.text
        after_progress = client.get(
            f"/v1/jobs/{job_id}/events",
            headers=DEVICE_AUTHORIZATION,
            params={"after_revision": 6},
        )
        assert [item["revision"] for item in after_progress.json()["items"]] == [7, 8]

    with TestClient(create_app(config)) as restarted:
        detail = restarted.get(f"/v1/jobs/{job_id}", headers=DEVICE_AUTHORIZATION)
        assert detail.status_code == 200
        assert detail.json()["status"] == "cancelled"
        assert detail.json()["revision"] == 8
        assert private_instruction not in detail.text
        assert private_follow_up not in detail.text
        assert private_live_follow_up not in detail.text


def test_foreground_api_remains_responsive_while_executor_process_is_running(tmp_path) -> None:
    launcher = HoldingLauncher()
    config = settings(tmp_path).model_copy(
        update={
            "job_executor_enabled": True,
            "job_executor_poll_seconds": 0.05,
            "job_executor_heartbeat_seconds": 0.05,
            "job_executor_lease_seconds": 2,
        }
    )
    with TestClient(create_app(config, job_launcher=launcher)) as client:
        enroll_and_create_session(client)
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-concurrent",
            arguments={"instruction": "private long-running job"},
        )
        assert accepted.status_code == 200
        job_id = accepted.json()["details"]["jobId"]
        deadline = time.monotonic() + 2
        while not launcher.processes and time.monotonic() < deadline:
            time.sleep(0.01)
        assert launcher.processes

        jobs = client.get("/v1/sessions/session-1/jobs", headers=DEVICE_AUTHORIZATION)
        configuration = client.get(
            "/v1/configuration",
            headers=DEVICE_AUTHORIZATION,
            params={"session_id": "session-1"},
        )
        assert jobs.status_code == 200
        assert jobs.json()["items"][0]["status"] == "running"
        assert configuration.status_code == 200

        cancelled = tool(
            client,
            tool_name="manage_job",
            call_id="cancel-concurrent",
            arguments={"operation": "cancel", "jobId": job_id},
        )
        assert cancelled.status_code == 200
        deadline = time.monotonic() + 2
        status = "running"
        while status != "cancelled" and time.monotonic() < deadline:
            status = client.get(f"/v1/jobs/{job_id}", headers=DEVICE_AUTHORIZATION).json()["status"]
            time.sleep(0.01)
        assert status == "cancelled"
        assert launcher.processes[0].terminated


def test_manage_job_retries_a_failed_job_as_a_fresh_attempt(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-retry",
            arguments={"instruction": "download and extract headlines"},
        )
        job_id = accepted.json()["details"]["jobId"]
        claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "retry-worker-1", "lease_seconds": 60},
        )
        assert claim.status_code == 200
        failed = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "retry-worker-1",
                "kind": "failed",
                "detail_code": "model_provider_unavailable",
            },
        )
        assert failed.status_code == 200

        retried = tool(
            client,
            tool_name="manage_job",
            call_id="retry-1",
            arguments={"operation": "retry", "jobId": job_id},
        )
        replay = tool(
            client,
            tool_name="manage_job",
            call_id="retry-1",
            arguments={"operation": "retry", "jobId": job_id},
        )

        assert retried.status_code == 200
        assert retried.json()["details"]["status"] == "queued"
        assert replay.status_code == 200
        assert replay.json()["details"]["replayed"] == "true"
        second_claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "retry-worker-2", "lease_seconds": 60},
        )
        assert second_claim.status_code == 200
        assert second_claim.json()["job"]["job_id"] == job_id
        assert second_claim.json()["job"]["attempt"] == 2


def test_manage_job_status_returns_only_the_promoted_result(tmp_path) -> None:
    workspace_root = tmp_path / "jobs"
    config = settings(tmp_path).model_copy(update={"job_workspace_root": workspace_root})
    private_instruction = "private instruction that must stay out of status"
    private_transcript = "private command transcript that must stay out of status"
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-result",
            arguments={"instruction": private_instruction},
        )
        job_id = accepted.json()["details"]["jobId"]
        claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "result-worker", "lease_seconds": 60},
        )
        assert claim.status_code == 200
        workspace = workspace_root / job_id / "attempt-1"
        workspace.mkdir(parents=True)
        workspace.joinpath("agent-transcript.jsonl").write_text(private_transcript)
        write_promoted_result(
            workspace,
            PromotedJobResult(
                schemaVersion=1,
                jobId=job_id,
                summary="Calculated and recurrence-checked the first 100 Fibonacci numbers.",
                artifacts=(
                    PromotedArtifact(
                        artifactId="artifact:file:fibonacci",
                        path="report.md",
                        sha256="a" * 64,
                        byteLength=123,
                    ),
                ),
            ),
        )
        succeeded = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "result-worker",
                "kind": "succeeded",
                "detail_code": "completed",
            },
        )
        assert succeeded.status_code == 200

        status = tool(
            client,
            tool_name="manage_job",
            call_id="status-result",
            arguments={"operation": "status", "jobId": job_id},
        )

        assert status.status_code == 200
        content = json.loads(status.json()["modelContent"])
        assert content["result"] == {
            "summary": "Calculated and recurrence-checked the first 100 Fibonacci numbers.",
            "artifacts": [
                {
                    "artifactId": "artifact:file:fibonacci",
                    "path": "report.md",
                    "sha256": "a" * 64,
                    "byteLength": 123,
                }
            ],
        }
        assert content["latestEvent"] == {
            "kind": "succeeded",
            "status": "succeeded",
            "detailCode": "completed",
            "revision": 3,
        }
        assert status.json()["details"]["artifactCount"] == "1"
        assert status.json()["details"]["latestDetailCode"] == "completed"
        assert private_instruction not in status.text
        assert private_transcript not in status.text


def test_new_job_snapshots_bounded_links_from_prior_success(tmp_path) -> None:
    workspace_root = tmp_path / "jobs"
    config = settings(tmp_path).model_copy(update={"job_workspace_root": workspace_root})
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        first = tool(
            client,
            tool_name="run_job",
            call_id="run-headlines",
            arguments={"instruction": "Collect the Hacker News headlines."},
        )
        first_job_id = first.json()["details"]["jobId"]
        claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "headlines-worker", "lease_seconds": 60},
        )
        assert claim.status_code == 200
        workspace = workspace_root / first_job_id / "attempt-1"
        workspace.mkdir(parents=True)
        write_promoted_result(
            workspace,
            PromotedJobResult(
                schemaVersion=1,
                jobId=first_job_id,
                summary="Collected the headlines.",
                relatedLinks=(
                    PromotedRelatedLink(
                        label="Second story",
                        locator="https://example.test/second-story",
                    ),
                ),
            ),
        )
        succeeded = client.post(
            f"/v1/internal/jobs/{first_job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "headlines-worker",
                "kind": "succeeded",
                "detail_code": "completed",
            },
        )
        assert succeeded.status_code == 200

        second = tool(
            client,
            tool_name="run_job",
            call_id="run-second-story",
            arguments={"instruction": "Download the requested story."},
        )
        second_job_id = second.json()["details"]["jobId"]

    with sqlite3.connect(config.database_path) as db:
        user_message, links_json = db.execute(
            """SELECT user_message, related_links_json
            FROM background_job_attempt_contexts WHERE job_id = ? AND attempt = 1""",
            (second_job_id,),
        ).fetchone()
    assert user_message == "verbatim user turn for run-second-story"
    assert json.loads(links_json) == [
        {"label": "Second story", "locator": "https://example.test/second-story"}
    ]


def test_unsourced_markdown_job_artifact_is_status_visible_and_readable(tmp_path) -> None:
    workspace_root = tmp_path / "jobs"
    config = settings(tmp_path).model_copy(update={"job_workspace_root": workspace_root})
    canonical = b"# Hacker News headlines\n\n1. First headline\n2. Second headline\n"
    digest = hashlib.sha256(canonical).hexdigest()
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-generated-source",
            arguments={"instruction": "Create the headlines Markdown file."},
        )
        job_id = accepted.json()["details"]["jobId"]
        claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "generated-source-worker", "lease_seconds": 60},
        )
        assert claim.status_code == 200
        workspace = workspace_root / job_id / "attempt-1"
        files = workspace / "files"
        files.mkdir(parents=True)
        path = "hacker_news_headlines.md"
        files.joinpath(path).write_bytes(canonical)
        artifact = PromotedArtifact(
            artifactId=f"artifact:file:{digest[:16]}",
            path=path,
            sha256=digest,
            byteLength=len(canonical),
        )
        write_promoted_result(
            workspace,
            PromotedJobResult(
                schemaVersion=1,
                jobId=job_id,
                summary="Created the requested headlines file.",
                artifacts=(artifact,),
            ),
        )
        succeeded = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "generated-source-worker",
                "kind": "succeeded",
                "detail_code": "completed",
                "artifact_ids": [artifact.artifact_id],
            },
        )
        assert succeeded.status_code == 200

        status = tool(
            client,
            tool_name="manage_job",
            call_id="status-generated-source",
            arguments={"operation": "status", "jobId": job_id},
        )
        assert status.status_code == 200
        content = json.loads(status.json()["modelContent"])
        assert content["result"]["sources"][0]["name"] == path
        assert status.json()["details"]["sourceCount"] == "1"

        response = tool(
            client,
            tool_name="read_job_source",
            call_id="read-generated-source",
            arguments={"reference": path, "jobId": job_id},
        )
        assert response.status_code == 200
        envelope = MessageEnvelope.from_proto(
            protocol_pb2.MessageEnvelope.FromString(
                base64.b64decode(
                    response.json()["details"]["sourceEnvelopeProtoBase64"],
                    validate=True,
                )
            )
        )
        assert envelope.content.inline_text.encode() == canonical
        assert envelope.provenance.locator.startswith("radcliffe-job-output:")


def test_run_job_snapshots_authorized_source_and_prior_html_artifact_inputs(tmp_path) -> None:
    workspace_root = tmp_path / "jobs"
    config = settings(tmp_path).model_copy(update={"job_workspace_root": workspace_root})
    foreground = b"# Local brief\n\nUse the already downloaded page.\n"
    config.source_root.joinpath("brief.md").write_bytes(foreground)
    html = b"<html><head><title>Staged result</title></head><body>Ten rows.</body></html>\n"
    digest = hashlib.sha256(html).hexdigest()
    alternate_one = b"<html><title>Alternate one</title></html>\n"
    alternate_two = b"<html><title>Alternate two</title></html>\n"
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        producer = tool(
            client,
            tool_name="run_job",
            call_id="produce-html",
            arguments={"instruction": "Download one page."},
        )
        assert producer.status_code == 200
        producer_id = producer.json()["details"]["jobId"]
        claimed = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "producer-worker", "lease_seconds": 60},
        )
        assert claimed.status_code == 200
        producer_workspace = workspace_root / producer_id / "attempt-1"
        producer_files = producer_workspace / "files"
        producer_files.mkdir(parents=True)
        producer_files.joinpath("downloaded-page.html").write_bytes(html)
        producer_files.joinpath("report-one.html").write_bytes(alternate_one)
        producer_files.joinpath("report_one.html").write_bytes(alternate_two)
        artifact = PromotedArtifact(
            artifactId=f"artifact:file:{digest[:16]}",
            path="downloaded-page.html",
            sha256=digest,
            byteLength=len(html),
        )
        ambiguous_artifacts = (
            PromotedArtifact(
                artifactId="artifact:file:alternate-one",
                path="report-one.html",
                sha256=hashlib.sha256(alternate_one).hexdigest(),
                byteLength=len(alternate_one),
            ),
            PromotedArtifact(
                artifactId="artifact:file:alternate-two",
                path="report_one.html",
                sha256=hashlib.sha256(alternate_two).hexdigest(),
                byteLength=len(alternate_two),
            ),
        )
        write_promoted_result(
            producer_workspace,
            PromotedJobResult(
                schemaVersion=1,
                jobId=producer_id,
                summary="Downloaded the requested page.",
                artifacts=(artifact, *ambiguous_artifacts),
            ),
        )
        succeeded = client.post(
            f"/v1/internal/jobs/{producer_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "producer-worker",
                "kind": "succeeded",
                "detail_code": "completed",
                "artifact_ids": [
                    artifact.artifact_id,
                    *(item.artifact_id for item in ambiguous_artifacts),
                ],
            },
        )
        assert succeeded.status_code == 200

        dependent = tool(
            client,
            tool_name="run_job",
            call_id="consume-inputs",
            arguments={
                "instruction": "Extract the title from the prior HTML without downloading it.",
                "inputs": [
                    {"kind": "source", "reference": "brief.md"},
                    {
                        "kind": "artifact",
                        "reference": "downloaded-page.html",
                        "jobId": producer_id,
                    },
                ],
            },
        )
        assert dependent.status_code == 200
        dependent_id = dependent.json()["details"]["jobId"]

        with sqlite3.connect(config.database_path) as database:
            rows = database.execute(
                """SELECT position, kind, name, source_session_id, source_reference,
                producer_job_id, artifact_id, sha256, byte_length, canonical_bytes
                FROM background_job_inputs WHERE job_id = ? ORDER BY position""",
                (dependent_id,),
            ).fetchall()
        assert [(row[1], row[2]) for row in rows] == [
            ("source", "input-1-brief.md"),
            ("artifact", "input-2-downloaded-page.html"),
        ]
        assert rows[0][3] == rows[1][3] == "session-1"
        assert rows[0][9] == foreground
        assert rows[1][5] == producer_id
        assert rows[1][6] == artifact.artifact_id
        assert rows[1][7] == digest
        assert rows[1][8] == len(html)
        assert rows[1][9] == html

        config.source_root.joinpath("brief.md").write_text("mutated after acceptance")
        producer_files.joinpath("downloaded-page.html").write_text("mutated after acceptance")
        with sqlite3.connect(config.database_path) as database:
            retained = database.execute(
                """SELECT canonical_bytes FROM background_job_inputs
                WHERE job_id = ? ORDER BY position""",
                (dependent_id,),
            ).fetchall()
        assert retained == [(foreground,), (html,)]

        traversal = tool(
            client,
            tool_name="run_job",
            call_id="reject-traversal-input",
            arguments={
                "instruction": "Try an unauthorized path.",
                "inputs": [{"kind": "source", "reference": "../brief.md"}],
            },
        )
        assert traversal.status_code == 403

        outside = tmp_path / "outside.md"
        outside.write_text("not authorized through a link")
        config.source_root.joinpath("linked.md").symlink_to(outside)
        symlink = tool(
            client,
            tool_name="run_job",
            call_id="reject-symlink-input",
            arguments={
                "instruction": "Try a symbolic-link source.",
                "inputs": [{"kind": "source", "reference": "linked.md"}],
            },
        )
        assert symlink.status_code == 403

        ambiguous = tool(
            client,
            tool_name="run_job",
            call_id="reject-ambiguous-input",
            arguments={
                "instruction": "Try an ambiguous prior artifact.",
                "inputs": [
                    {
                        "kind": "artifact",
                        "reference": "report one html",
                        "jobId": producer_id,
                    }
                ],
            },
        )
        assert ambiguous.status_code == 409

        stale_artifact = tool(
            client,
            tool_name="run_job",
            call_id="reject-mutable-artifact",
            arguments={
                "instruction": "Try a mutable prior artifact.",
                "inputs": [
                    {
                        "kind": "artifact",
                        "reference": artifact.artifact_id,
                        "jobId": producer_id,
                    }
                ],
            },
        )
        assert stale_artifact.status_code == 404


def test_named_job_source_resolves_to_exact_hash_verified_rendering(tmp_path) -> None:
    workspace_root = tmp_path / "jobs"
    config = settings(tmp_path).model_copy(update={"job_workspace_root": workspace_root})
    canonical = b"# Scaling Laws for Neural Language Models\n\nExact abstract bytes.\n"
    digest = hashlib.sha256(canonical).hexdigest()
    other_canonical = b"# Scaling Laws for Vision\n\nA different exact abstract.\n"
    other_digest = hashlib.sha256(other_canonical).hexdigest()
    with TestClient(create_app(config)) as client:
        enroll_and_create_session(client)
        accepted = tool(
            client,
            tool_name="run_job",
            call_id="run-paper",
            arguments={"instruction": "Find the scaling laws paper."},
        )
        job_id = accepted.json()["details"]["jobId"]
        claim = client.post(
            "/v1/internal/jobs/claim",
            headers=INTERNAL_AUTHORIZATION,
            json={"lease_owner": "paper-worker", "lease_seconds": 60},
        )
        assert claim.status_code == 200
        workspace = workspace_root / job_id / "attempt-1"
        files = workspace / "files"
        files.mkdir(parents=True)
        files.joinpath("scaling-laws.md").write_bytes(canonical)
        files.joinpath("vision-scaling-laws.md").write_bytes(other_canonical)
        artifact = PromotedArtifact(
            artifactId=f"artifact:file:{digest[:16]}",
            path="scaling-laws.md",
            sha256=digest,
            byteLength=len(canonical),
        )
        source = PromotedSource(
            entityId=f"source:{digest[:16]}",
            name="Scaling Laws for Neural Language Models",
            locator="https://example.test/scaling-laws",
            artifactId=artifact.artifact_id,
            path=artifact.path,
            sha256=digest,
            byteLength=len(canonical),
        )
        other_artifact = PromotedArtifact(
            artifactId=f"artifact:file:{other_digest[:16]}",
            path="vision-scaling-laws.md",
            sha256=other_digest,
            byteLength=len(other_canonical),
        )
        other_source = PromotedSource(
            entityId=f"source:{other_digest[:16]}",
            name="Scaling Laws for Vision",
            locator="https://example.test/vision-scaling-laws",
            artifactId=other_artifact.artifact_id,
            path=other_artifact.path,
            sha256=other_digest,
            byteLength=len(other_canonical),
        )
        direct_finding = PromotedFinding(
            entityId=f"finding:{digest[:16]}",
            text="The paper reports exact abstract bytes.",
            sourceEntityIds=(source.entity_id,),
            inference=False,
        )
        inferred_finding = PromotedFinding(
            entityId=f"finding:{other_digest[:16]}",
            text="The two papers likely share a scaling-law methodology.",
            sourceEntityIds=(source.entity_id, other_source.entity_id),
            inference=True,
        )
        write_promoted_result(
            workspace,
            PromotedJobResult(
                schemaVersion=1,
                jobId=job_id,
                summary="Found the requested paper.",
                artifacts=(artifact, other_artifact),
                sources=(source, other_source),
                findings=(direct_finding, inferred_finding),
            ),
        )
        succeeded = client.post(
            f"/v1/internal/jobs/{job_id}/events",
            headers=INTERNAL_AUTHORIZATION,
            json={
                "lease_owner": "paper-worker",
                "kind": "succeeded",
                "detail_code": "completed",
                "entity_ids": [
                    source.entity_id,
                    other_source.entity_id,
                    direct_finding.entity_id,
                    inferred_finding.entity_id,
                ],
                "artifact_ids": [artifact.artifact_id, other_artifact.artifact_id],
            },
        )
        assert succeeded.status_code == 200

        job_status = tool(
            client,
            tool_name="manage_job",
            call_id="status-paper",
            arguments={"operation": "status", "jobId": job_id},
        )
        assert job_status.status_code == 200
        result = json.loads(job_status.json()["modelContent"])["result"]
        assert result["findings"] == [
            {
                "entityId": direct_finding.entity_id,
                "text": direct_finding.text,
                "sourceEntityIds": [source.entity_id],
                "inference": False,
            },
            {
                "entityId": inferred_finding.entity_id,
                "text": inferred_finding.text,
                "sourceEntityIds": [source.entity_id, other_source.entity_id],
                "inference": True,
            },
        ]
        assert job_status.json()["details"]["findingCount"] == "2"

        response = tool(
            client,
            tool_name="read_job_source",
            call_id="read-paper",
            arguments={"reference": source.name, "rate": "fast"},
        )

        assert response.status_code == 200
        body = response.json()
        envelope = MessageEnvelope.from_proto(
            protocol_pb2.MessageEnvelope.FromString(
                base64.b64decode(body["details"]["sourceEnvelopeProtoBase64"], validate=True)
            )
        )
        assert envelope.source is SourceKind.TEXT_SOURCE
        assert envelope.content.inline_text.encode() == canonical
        assert envelope.content_sha256.hex() == digest
        assert envelope.provenance.display_name == source.name
        assert envelope.provenance.locator == source.locator
        assert envelope.provenance.extractor == "promoted-job-source"
        assert body["details"]["contentSha256"] == digest

        ambiguous = tool(
            client,
            tool_name="read_job_source",
            call_id="read-ambiguous-paper",
            arguments={"reference": "scaling laws"},
        )
        assert ambiguous.status_code == 409
        assert "ambiguous" in ambiguous.text
        assert source.name in ambiguous.text
        assert other_source.name in ambiguous.text
