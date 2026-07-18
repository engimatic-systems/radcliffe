import asyncio
import base64

from fastapi.testclient import TestClient
from google.protobuf.timestamp_pb2 import Timestamp
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.store import EventStore
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

DEVICE_CREDENTIAL = "rdc_dev_" + "d" * 43
DEVICE_AUTHORIZATION = {"Authorization": f"Bearer {DEVICE_CREDENTIAL}"}
INTERNAL_AUTHORIZATION = {"Authorization": "Bearer internal-token-1234"}


class FakeProcess:
    next_pid = 5000

    def __init__(self) -> None:
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode: int | None = None
        self.finished = asyncio.Event()
        self.terminated = False

    async def wait(self) -> int:
        await self.finished.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.finished.set()

    def kill(self) -> None:
        self.returncode = -9
        self.finished.set()


def settings(tmp_path, *, managed_workers_enabled: bool = False) -> Settings:
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
        managed_workers_enabled=managed_workers_enabled,
    )


def enroll(client: TestClient) -> str:
    response = client.post(
        "/v1/devices/enroll",
        headers={"Authorization": "Bearer bootstrap-token-123"},
        json={
            "installation_id": "session-actions-installation",
            "display_name": "Session action Pixel",
            "credential": DEVICE_CREDENTIAL,
        },
    )
    assert response.status_code == 200
    return response.json()["participant_identity"]


def create_session(
    client: TestClient,
    session_id: str,
    *,
    title: str | None = None,
) -> None:
    response = client.post(
        "/v1/sessions",
        headers=DEVICE_AUTHORIZATION,
        json={
            "session_id": session_id,
            "participant_identity": "ignored-by-enrollment",
            "title": title,
        },
    )
    assert response.status_code == 200


def tool(
    client: TestClient,
    *,
    session_id: str,
    call_id: str,
    arguments: dict[str, object],
):
    return client.post(
        "/v1/tool-calls",
        headers=INTERNAL_AUTHORIZATION,
        json={
            "sessionId": session_id,
            "generation": 1,
            "operationId": f"session-operation-{call_id}",
            "cancellationId": f"session-cancellation-{call_id}",
            "traceId": "trc_" + "e" * 32,
            "turnId": f"turn-{call_id}",
            "callId": call_id,
            "idempotencyKey": f"session-action:{call_id}",
            "toolName": "manage_session",
            "arguments": arguments,
        },
    )


def confirm(
    client: TestClient,
    pending_response,
    participant_identity: str,
    *,
    approved: bool = True,
):
    request = protocol_pb2.AgentControlActionRequest.FromString(
        base64.b64decode(pending_response.json()["details"]["actionRequestProtoBase64"])
    )
    # Retried confirmations must resend the exact same typed envelope.
    occurred_at = Timestamp(seconds=1)
    confirmation = protocol_pb2.AgentControlConfirmation(
        schema_version=1,
        confirmation_id=f"confirmation:{request.request_id}",
        request_id=request.request_id,
        operation=request.operation,
        origin=protocol_pb2.AgentControlOrigin(
            source=protocol_pb2.SOURCE_KIND_USER,
            actor_id=participant_identity,
            source_message_id=f"user:{request.request_id}",
        ),
        approved=approved,
        idempotency_key=f"confirmation-key:{request.request_id}:{approved}",
        occurred_at=occurred_at,
    )
    return client.post(
        "/v1/internal/agent-control/confirmations",
        headers=INTERNAL_AUTHORIZATION,
        json={
            "confirmation_proto_base64": base64.b64encode(
                confirmation.SerializeToString(deterministic=True)
            ).decode("ascii"),
            "participant_identity": participant_identity,
        },
    )


def test_create_is_server_owned_content_free_and_durably_idempotent(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        enroll(client)
        create_session(client, "source")
        asyncio.run(
            EventStore(config.database_path).append(
                MessageEnvelope.text(
                    session_id="source",
                    sequence=1,
                    source=SourceKind.USER,
                    text="private source history",
                )
            )
        )

        created = tool(
            client,
            session_id="source",
            call_id="create-1",
            arguments={"operation": "create"},
        )
        replay = tool(
            client,
            session_id="source",
            call_id="create-1",
            arguments={"operation": "create"},
        )

        assert created.status_code == 200
        assert replay.status_code == 200
        destination = created.json()["details"]["destinationSessionId"]
        assert destination.startswith("ses_")
        assert replay.json()["details"]["destinationSessionId"] == destination
        assert replay.json()["details"]["replayed"] == "true"
        assert (
            client.get("/v1/sessions/source", headers=DEVICE_AUTHORIZATION).json()["status"]
            == "ended"
        )
        assert (
            client.get(f"/v1/sessions/{destination}", headers=DEVICE_AUTHORIZATION).json()["status"]
            == "active"
        )
        assert (
            client.get(f"/v1/sessions/{destination}/messages", headers=DEVICE_AUTHORIZATION).json()[
                "items"
            ]
            == []
        )


def test_completed_create_replays_after_control_plane_restart(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        enroll(client)
        create_session(client, "source")
        created = tool(
            client,
            session_id="source",
            call_id="create-after-restart",
            arguments={"operation": "create"},
        )
        assert created.status_code == 200
        destination = created.json()["details"]["destinationSessionId"]

    with TestClient(create_app(config)) as restarted:
        replay = tool(
            restarted,
            session_id="source",
            call_id="create-after-restart",
            arguments={"operation": "create"},
        )

        assert replay.status_code == 200
        assert replay.json()["details"]["destinationSessionId"] == destination
        assert replay.json()["details"]["replayed"] == "true"


def test_switch_resolution_is_bounded_and_enrolled_confirmation_is_replayable(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        participant_identity = enroll(client)
        create_session(client, "source")
        create_session(client, "project-a", title="Project notes")
        create_session(client, "project-b", title="Project notes")

        ambiguous = tool(
            client,
            session_id="source",
            call_id="ambiguous",
            arguments={"operation": "switch", "reference": "Project notes"},
        )
        pending = tool(
            client,
            session_id="source",
            call_id="switch-1",
            arguments={"operation": "switch", "reference": "project-a"},
        )
        unauthorized = confirm(client, pending, "radcliffe-unenrolled-device")
        applied = confirm(client, pending, participant_identity)
        replay = confirm(client, pending, participant_identity)

        assert ambiguous.status_code == 200
        choices = ambiguous.json()["modelContent"]
        assert "ambiguous" in choices
        assert choices.count("session_id") == 2
        assert pending.json()["details"]["sessionTransitionStatus"] == ("awaiting_confirmation")
        assert unauthorized.status_code == 403
        assert applied.status_code == 200
        assert applied.json()["details"]["destinationSessionId"] == "project-a"
        assert replay.status_code == 200
        assert replay.json()["details"]["replayed"] == "true"
        assert (
            client.get("/v1/sessions/source", headers=DEVICE_AUTHORIZATION).json()["status"]
            == "ended"
        )


def test_restart_and_end_preserve_history_and_support_rejection(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        participant_identity = enroll(client)
        create_session(client, "restart-source")
        pending_restart = tool(
            client,
            session_id="restart-source",
            call_id="restart-1",
            arguments={"operation": "restart"},
        )
        restarted = confirm(client, pending_restart, participant_identity)
        destination = restarted.json()["details"]["destinationSessionId"]
        reopened = client.post(
            f"/v1/sessions/{destination}/credentials",
            headers=DEVICE_AUTHORIZATION,
            json={"participant_identity": "ignored-by-enrollment"},
        )
        assert reopened.status_code == 200

        rejected_end = tool(
            client,
            session_id=destination,
            call_id="end-rejected",
            arguments={"operation": "end"},
        )
        cancelled = confirm(
            client,
            rejected_end,
            participant_identity,
            approved=False,
        )
        pending_end = tool(
            client,
            session_id=destination,
            call_id="end-approved",
            arguments={"operation": "end"},
        )
        ended = confirm(client, pending_end, participant_identity)

        assert destination.startswith("ses_")
        assert (
            client.get("/v1/sessions/restart-source", headers=DEVICE_AUTHORIZATION).json()["status"]
            == "ended"
        )
        assert cancelled.json()["details"]["sessionTransitionStatus"] == "cancelled"
        assert ended.json()["details"]["sessionAction"] == "end"
        assert ended.json()["details"]["destinationSessionId"] == ""
        assert (
            client.get(f"/v1/sessions/{destination}", headers=DEVICE_AUTHORIZATION).json()["status"]
            == "ended"
        )


def test_worker_launch_failure_leaves_source_active_and_no_worker_orphan(tmp_path) -> None:
    config = settings(tmp_path, managed_workers_enabled=True)

    async def fail(_record):
        raise OSError("worker executable unavailable")

    with TestClient(create_app(config, worker_launcher=fail)) as client:
        enroll(client)
        create_session(client, "external-source")

        response = tool(
            client,
            session_id="external-source",
            call_id="failed-create",
            arguments={"operation": "create"},
        )

        assert response.status_code == 503
        assert (
            client.get("/v1/sessions/external-source", headers=DEVICE_AUTHORIZATION).json()[
                "status"
            ]
            == "active"
        )
        generated = [
            item
            for item in client.get(
                "/v1/sessions?include_archived=true",
                headers=DEVICE_AUTHORIZATION,
            ).json()["items"]
            if item["session_id"] != "external-source"
        ]
        assert len(generated) == 1
        assert generated[0]["status"] == "ended"
        assert generated[0]["worker_status"] == "stopped"


def test_create_starts_exactly_one_managed_worker_across_replay(tmp_path) -> None:
    config = settings(tmp_path, managed_workers_enabled=True)
    launched: list[FakeProcess] = []

    async def launch(_record):
        process = FakeProcess()
        launched.append(process)
        return process

    with TestClient(create_app(config, worker_launcher=launch)) as client:
        enroll(client)
        create_session(client, "external-source")
        first = tool(
            client,
            session_id="external-source",
            call_id="managed-create",
            arguments={"operation": "create"},
        )
        replay = tool(
            client,
            session_id="external-source",
            call_id="managed-create",
            arguments={"operation": "create"},
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert len(launched) == 1
