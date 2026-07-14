from fastapi.testclient import TestClient
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings


def settings(tmp_path) -> Settings:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    return Settings(
        livekit_api_key="test-key",
        livekit_api_secret="test-secret-that-is-definitely-long-enough",
        bootstrap_token="bootstrap-token-123",
        internal_token="internal-token-1234",
        database_path=tmp_path / "events.sqlite3",
        source_root=source_root,
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
