import asyncio
from collections.abc import Mapping

from fastapi.testclient import TestClient
from livekit import api
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.projection import (
    TURN_DISPOSITION_EVENT,
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
)
from radcliffe_domain.store import EventStore

AUTHORIZATION = {"Authorization": "Bearer bootstrap-token-123"}


class FakeContextUsageClient:
    def __init__(self) -> None:
        self.requests: list[Mapping[str, object]] = []

    async def measure(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        packets = request["packets"]
        assert isinstance(packets, list)
        tokens = sum(
            len(str(packet["content"]))
            for packet in packets
            if isinstance(packet, dict) and packet["include"]
        )
        return {
            "modelId": "qwen3:8b",
            "contextLimit": 1000,
            "projectedInputTokens": tokens,
            "reservedOutputTokens": 100,
            "remainingTokens": max(0, 900 - tokens),
            "percentage": min(100, tokens / 9),
            "projectionVersion": "context-v2",
            "measurementBasis": "test-exact-model-request",
            "exact": True,
            "overflow": tokens > 900,
            "requestDigest": "a" * 64,
        }


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.finished = asyncio.Event()
        self.pid = 1000
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
        managed_workers_enabled=False,
    )


def create(client: TestClient, **values):
    request = {"participant_identity": "pixel", **values}
    return client.post("/v1/sessions", headers=AUTHORIZATION, json=request)


def test_generated_session_idempotency_and_least_privilege_reopen(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        first = create(
            client,
            idempotency_key="android-create-1",
            title="Morning session",
        )
        retry = create(
            client,
            idempotency_key="android-create-1",
            title="Retry must not overwrite",
        )

        assert first.status_code == 200
        assert retry.status_code == 200
        body = first.json()
        retry_body = retry.json()
        assert body["session_id"].startswith("ses_")
        assert retry_body["session_id"] == body["session_id"]
        assert retry_body["title"] == "Morning session"
        assert retry_body["participant_token"] != body["participant_token"]
        assert "content" not in body

        claims = api.TokenVerifier(
            config.livekit_api_key,
            config.livekit_api_secret,
        ).verify(body["participant_token"])
        assert claims.identity == "pixel"
        assert claims.video.room_join
        assert claims.video.room == body["session_id"]
        assert claims.video.can_publish_sources == ["microphone"]
        assert not claims.video.room_admin

        reopened = client.post(
            f"/v1/sessions/{body['session_id']}/credentials",
            headers=AUTHORIZATION,
            json={"participant_identity": "pixel-reopened"},
        )
        assert reopened.status_code == 200
        assert reopened.json()["participant_token"] not in {
            body["participant_token"],
            retry_body["participant_token"],
        }


def test_catalog_list_detail_update_pagination_and_authorization(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/v1/sessions").status_code == 401
        assert create(client, session_id="legacy-a", title="First").status_code == 200
        assert create(client, session_id="legacy-b", title="Second").status_code == 200
        assert create(client, session_id="legacy-c", title="Third").status_code == 200

        first_page = client.get("/v1/sessions?limit=2", headers=AUTHORIZATION)
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert [item["session_id"] for item in first_body["items"]] == [
            "legacy-c",
            "legacy-b",
        ]
        assert first_body["next_cursor"]
        second_page = client.get(
            "/v1/sessions",
            headers=AUTHORIZATION,
            params={"limit": 2, "cursor": first_body["next_cursor"]},
        )
        assert [item["session_id"] for item in second_page.json()["items"]] == ["legacy-a"]
        assert second_page.json()["next_cursor"] is None

        detail = client.get("/v1/sessions/legacy-b", headers=AUTHORIZATION)
        assert detail.json()["title"] == "Second"
        assert set(detail.json()) == {
            "session_id",
            "created_at",
            "last_activity_at",
            "title",
            "status",
            "protocol_version",
            "generation",
            "worker_status",
        }
        updated = client.patch(
            "/v1/sessions/legacy-b",
            headers=AUTHORIZATION,
            json={"title": "Renamed", "status": "archived"},
        )
        assert updated.json()["title"] == "Renamed"
        assert updated.json()["status"] == "archived"
        assert client.get("/v1/sessions?cursor=not-valid", headers=AUTHORIZATION).status_code == 422
        assert client.get("/v1/sessions/missing", headers=AUTHORIZATION).status_code == 404


def test_catalog_survives_control_plane_restart(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        response = create(client, title="Durable")
        session_id = response.json()["session_id"]

    with TestClient(create_app(config)) as restarted:
        detail = restarted.get(f"/v1/sessions/{session_id}", headers=AUTHORIZATION)
        assert detail.status_code == 200
        assert detail.json()["title"] == "Durable"
        credential = restarted.post(
            f"/v1/sessions/{session_id}/credentials",
            headers=AUTHORIZATION,
            json={"participant_identity": "worker", "role": "worker"},
        )
        assert credential.status_code == 200
        assert credential.json()["generation"] == 1


def test_context_usage_projects_current_history_without_leaking_content(tmp_path) -> None:
    config = settings(tmp_path)
    usage = FakeContextUsageClient()
    with TestClient(create_app(config, context_usage_client=usage)) as client:
        created = create(client, session_id="context-session")
        assert created.status_code == 200
        store = EventStore(config.database_path)
        envelopes = (
            MessageEnvelope.text(
                session_id="context-session",
                sequence=1,
                source=SourceKind.USER,
                text="abandoned private request",
                metadata={TURN_ID_METADATA: "old-turn"},
            ),
            MessageEnvelope.text(
                session_id="context-session",
                sequence=2,
                source=SourceKind.SYSTEM,
                text="interrupted",
                metadata={
                    TURN_ID_METADATA: "old-turn",
                    TURN_EVENT_METADATA: TURN_DISPOSITION_EVENT,
                    TURN_DISPOSITION_METADATA: "interrupted",
                },
            ),
            MessageEnvelope.text(
                session_id="context-session",
                sequence=3,
                source=SourceKind.CALL,
                text='{"tool":"read_source","outcome":"authorized"}',
            ),
            MessageEnvelope.text(
                session_id="context-session",
                sequence=4,
                source=SourceKind.TEXT_SOURCE,
                text="Canonical source text.",
            ),
        )
        for envelope in envelopes:
            asyncio.run(store.append(envelope))

        assert client.get("/v1/sessions/context-session/context-usage").status_code == 401
        first = client.get(
            "/v1/sessions/context-session/context-usage",
            headers=AUTHORIZATION,
        )

        assert first.status_code == 200
        body = first.json()
        assert body == {
            "session_id": "context-session",
            "model_id": "qwen3:8b",
            "context_limit": 1000,
            "projected_input_tokens": 67,
            "reserved_output_tokens": 100,
            "remaining_tokens": 833,
            "percentage": 67 / 9,
            "projection_version": "context-v2",
            "measurement_basis": "test-exact-model-request",
            "exact": True,
            "overflow": False,
        }
        assert "content" not in body
        assert "request_digest" not in body
        packets = usage.requests[-1]["packets"]
        assert isinstance(packets, list)
        assert [packet["include"] for packet in packets] == [False, False, True, True]
        assert packets[2]["role"] == "tool"
        assert packets[3]["role"] == "source"

        asyncio.run(
            store.append(
                MessageEnvelope.text(
                    session_id="context-session",
                    sequence=5,
                    source=SourceKind.USER,
                    text="new request",
                )
            )
        )
        second = client.get(
            "/v1/sessions/context-session/context-usage",
            headers=AUTHORIZATION,
        )
        assert second.json()["projected_input_tokens"] == 78
        assert usage.requests[-1]["requestId"] == "context-usage:context-session:5"
        assert (
            client.get("/v1/sessions/missing/context-usage", headers=AUTHORIZATION).status_code
            == 404
        )


def test_generated_session_dispatches_one_worker_and_end_cleans_it_up(tmp_path) -> None:
    config = settings(tmp_path).model_copy(
        update={"managed_workers_enabled": True, "worker_reconcile_seconds": 3600}
    )
    launched: list[FakeProcess] = []

    async def launch(_record):
        process = FakeProcess()
        launched.append(process)
        return process

    with TestClient(create_app(config, worker_launcher=launch)) as client:
        first = create(client, idempotency_key="managed-create")
        retry = create(client, idempotency_key="managed-create")

        assert first.status_code == 200
        assert first.json()["worker_status"] == "running"
        assert retry.json()["session_id"] == first.json()["session_id"]
        assert len(launched) == 1
        ended = client.patch(
            f"/v1/sessions/{first.json()['session_id']}",
            headers=AUTHORIZATION,
            json={"status": "ended"},
        )
        assert ended.json()["worker_status"] == "stopped"
        assert launched[0].terminated


def test_supervisor_restart_reconciles_recent_managed_session(tmp_path) -> None:
    config = settings(tmp_path).model_copy(
        update={"managed_workers_enabled": True, "worker_reconcile_seconds": 3600}
    )
    first_launches: list[FakeProcess] = []
    second_launches: list[FakeProcess] = []

    async def first_launcher(_record):
        process = FakeProcess()
        first_launches.append(process)
        return process

    async def second_launcher(_record):
        process = FakeProcess()
        second_launches.append(process)
        return process

    with TestClient(create_app(config, worker_launcher=first_launcher)) as client:
        response = create(client, idempotency_key="restart-worker")
        session_id = response.json()["session_id"]
        assert len(first_launches) == 1

    assert first_launches[0].terminated
    with TestClient(create_app(config, worker_launcher=second_launcher)) as restarted:
        detail = restarted.get(f"/v1/sessions/{session_id}", headers=AUTHORIZATION)
        assert detail.json()["worker_status"] == "running"
        assert len(second_launches) == 1
