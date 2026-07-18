import base64
import json

from fastapi.testclient import TestClient
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


def settings(tmp_path) -> Settings:
    source_root = tmp_path / "sources"
    source_root.mkdir(exist_ok=True)
    return Settings(
        livekit_api_key="test-key",
        livekit_api_secret="test-secret-that-is-definitely-long-enough",
        bootstrap_token="bootstrap-token-123",
        internal_token="internal-token-1234",
        database_path=tmp_path / "events.sqlite3",
        source_root=source_root,
        managed_workers_enabled=False,
    )


def test_health(tmp_path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_session_token_is_room_scoped_and_tool_content_bypasses_model(tmp_path) -> None:
    config = settings(tmp_path)
    fixture = config.source_root / "demo.md"
    fixture.write_text("# Exact\n\nCanonical source text.\n")
    with TestClient(create_app(config)) as client:
        unauthorized = client.post(
            "/v1/sessions",
            json={"session_id": "session-1", "participant_identity": "pixel"},
        )
        assert unauthorized.status_code == 401
        session = client.post(
            "/v1/sessions",
            headers={"Authorization": "Bearer bootstrap-token-123"},
            json={"session_id": "session-1", "participant_identity": "pixel"},
        )
        assert session.status_code == 200
        payload = session.json()
        assert payload["room_name"] == "session-1"
        assert payload["protocol_version"] == 2
        assert payload["expires_in_seconds"] == 600
        assert payload["worker_status"] == "external"

        catalog = client.post(
            "/v1/tool-calls",
            headers={"Authorization": "Bearer internal-token-1234"},
            json={
                "session_id": "session-1",
                "generation": 1,
                "call_id": "catalog-1",
                "idempotency_key": "catalog-1",
                "tool_name": "list_sources",
                "arguments": {},
            },
        )
        assert catalog.status_code == 200
        catalog_body = catalog.json()
        assert json.loads(catalog_body["modelContent"]) == {
            "paths": ["demo.md"],
            "truncated": False,
        }
        assert catalog_body["details"] == {
            "catalogCount": "1",
            "truncated": "false",
        }
        assert str(config.source_root) not in catalog_body["modelContent"]

        mismatched_arguments = client.post(
            "/v1/tool-calls",
            headers={"Authorization": "Bearer internal-token-1234"},
            json={
                "session_id": "session-1",
                "generation": 1,
                "call_id": "bad-catalog",
                "idempotency_key": "bad-catalog",
                "tool_name": "list_sources",
                "arguments": {"path": "demo.md"},
            },
        )
        assert mismatched_arguments.status_code == 422

        tool = client.post(
            "/v1/tool-calls",
            headers={"Authorization": "Bearer internal-token-1234"},
            json={
                "session_id": "session-1",
                "generation": 1,
                "call_id": "call-1",
                "idempotency_key": "idem-1",
                "tool_name": "read_source",
                "arguments": {"path": "demo.md"},
            },
        )
        assert tool.status_code == 200
        body = tool.json()
        assert "Canonical source text" not in body["modelContent"]
        assert body["details"]["sourceMessageId"] == "source:call-1"
        source = MessageEnvelope.from_proto(
            protocol_pb2.MessageEnvelope.FromString(
                base64.b64decode(body["details"]["sourceEnvelopeProtoBase64"], validate=True)
            )
        )
        assert source.source is SourceKind.TEXT_SOURCE
        assert source.content.inline_text == "# Exact\n\nCanonical source text.\n"
        assert source.content_sha256.hex() == body["details"]["contentSha256"]
        assert source.provenance.locator == body["details"]["locator"]

        camel_case_tool = client.post(
            "/v1/tool-calls",
            headers={"Authorization": "Bearer internal-token-1234"},
            json={
                "sessionId": "session-1",
                "generation": 1,
                "callId": "call-2",
                "idempotencyKey": "idem-2",
                "toolName": "read_source",
                "arguments": {"path": "demo.md"},
            },
        )
        assert camel_case_tool.status_code == 200

        next_generation = client.post(
            "/v1/tool-calls",
            headers={"Authorization": "Bearer internal-token-1234"},
            json={
                "sessionId": "session-1",
                "generation": 2,
                "callId": "call-3",
                "idempotencyKey": "idem-3",
                "toolName": "read_source",
                "arguments": {"path": "demo.md"},
            },
        )
        assert next_generation.status_code == 200

        stale_generation = client.post(
            "/v1/tool-calls",
            headers={"Authorization": "Bearer internal-token-1234"},
            json={
                "sessionId": "session-1",
                "generation": 1,
                "callId": "call-4",
                "idempotencyKey": "idem-4",
                "toolName": "read_source",
                "arguments": {"path": "demo.md"},
            },
        )
        assert stale_generation.status_code == 403
        assert stale_generation.json()["detail"] == "stale operation generation"
