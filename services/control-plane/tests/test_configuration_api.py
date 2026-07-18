import asyncio

import aiosqlite
from fastapi.testclient import TestClient
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings

DEVICE_ONE = "rdc_dev_" + "a" * 43
DEVICE_TWO = "rdc_dev_" + "b" * 43


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
        telemetry_path=tmp_path / "telemetry.jsonl",
        managed_workers_enabled=False,
    )


def enroll(client: TestClient, credential: str, installation: str) -> dict[str, str]:
    response = client.post(
        "/v1/devices/enroll",
        headers={"Authorization": "Bearer bootstrap-token-123"},
        json={
            "installation_id": installation,
            "display_name": installation,
            "credential": credential,
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {credential}"}


def create_session(client: TestClient, authorization: dict[str, str]) -> None:
    response = client.post(
        "/v1/sessions",
        headers=authorization,
        json={"session_id": "session-1", "participant_identity": "pixel"},
    )
    assert response.status_code == 200


def mutation(
    *,
    operation: str = "set",
    key: str = "response_detail",
    scope: str = "session",
    value: str | None = "detailed",
    idempotency_key: str = "configuration-1",
    expected_revision: int | None = 0,
    undo_mutation_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": operation,
        "key": key,
        "scope": scope,
        "session_id": "session-1",
        "idempotency_key": idempotency_key,
        "expected_revision": expected_revision,
    }
    if value is not None:
        payload["value"] = value
    if undo_mutation_id is not None:
        payload["undo_mutation_id"] = undo_mutation_id
    return payload


def test_authenticated_configuration_api_replays_conflicts_resets_and_undoes(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        device_one = enroll(client, DEVICE_ONE, "configuration-installation-one")
        device_two = enroll(client, DEVICE_TWO, "configuration-installation-two")
        create_session(client, device_one)

        defaults = client.get(
            "/v1/configuration",
            headers=device_one,
            params={"session_id": "session-1"},
        )
        assert defaults.status_code == 200
        indexed = {item["key"]: item for item in defaults.json()["items"]}
        assert indexed["response_detail"]["value"] == "standard"
        assert indexed["default_speech_rate"]["value"] == "1.0x"
        assert indexed["default_speech_rate"]["allowed_values"] == [
            "0.75x",
            "1.0x",
            "1.25x",
            "1.5x",
        ]
        assert "api_key" not in defaults.text
        assert DEVICE_ONE not in defaults.text

        applied = client.post(
            "/v1/configuration/mutations",
            headers=device_one,
            json=mutation(),
        )
        assert applied.status_code == 200
        assert applied.json()["effective"]["value"] == "detailed"
        assert applied.json()["effective"]["effective_scope"] == "session"

        replay = client.post(
            "/v1/configuration/mutations",
            headers=device_one,
            json=mutation(),
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["mutation_id"] == applied.json()["mutation_id"]

        changed_replay = client.post(
            "/v1/configuration/mutations",
            headers=device_one,
            json=mutation(value="brief"),
        )
        assert changed_replay.status_code == 409

        stale_device = client.post(
            "/v1/configuration/mutations",
            headers=device_two,
            json=mutation(idempotency_key="configuration-2", value="brief"),
        )
        assert stale_device.status_code == 409

        undo = client.post(
            "/v1/configuration/mutations",
            headers=device_one,
            json=mutation(
                operation="undo",
                value=None,
                idempotency_key="configuration-undo",
                expected_revision=1,
                undo_mutation_id=applied.json()["mutation_id"],
            ),
        )
        assert undo.status_code == 200
        assert undo.json()["effective"]["value"] == "standard"

        persisted = client.get(
            "/v1/configuration",
            headers=device_one,
            params={"session_id": "session-1"},
        )
        assert persisted.json()["items"][0]["value"] == "standard"

        denied = client.post(
            "/v1/configuration/mutations",
            headers=device_one,
            json={**mutation(), "key": "device_credential"},
        )
        assert denied.status_code == 422


def test_agent_configure_tool_uses_action_contract_and_turn_scope(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        authorization = enroll(client, DEVICE_ONE, "configuration-tool-install")
        create_session(client, authorization)
        headers = {"Authorization": "Bearer internal-token-1234"}

        def tool(call_id: str, arguments: dict[str, object], turn_id: str = "turn-1"):
            return client.post(
                "/v1/tool-calls",
                headers=headers,
                json={
                    "sessionId": "session-1",
                    "generation": 1,
                    "operationId": "configuration-operation-1",
                    "cancellationId": "configuration-cancellation-1",
                    "traceId": "trc_" + "c" * 32,
                    "turnId": turn_id,
                    "callId": call_id,
                    "idempotencyKey": f"configuration:{call_id}",
                    "toolName": "configure",
                    "arguments": arguments,
                },
            )

        applied = tool(
            "configure-1",
            {
                "operation": "set",
                "key": "default_speech_rate",
                "scope": "turn",
                "value": "1.25x",
            },
        )
        assert applied.status_code == 200
        assert applied.json()["details"] == {
            "key": "default_speech_rate",
            "value": "1.25x",
            "effectiveScope": "turn",
            "revision": "1",
            "operation": "set",
            "mutationId": "configuration:configure-1",
            "replayed": "false",
        }
        replay = tool(
            "configure-1",
            {
                "operation": "set",
                "key": "default_speech_rate",
                "scope": "turn",
                "value": "1.25x",
            },
        )
        assert replay.status_code == 200
        assert replay.json()["details"]["replayed"] == "true"

        expired = tool(
            "configure-2",
            {
                "operation": "inspect",
                "key": "default_speech_rate",
                "scope": "turn",
            },
            turn_id="turn-2",
        )
        assert expired.status_code == 200
        assert expired.json()["details"]["value"] == "1.0x"

        denied = tool(
            "configure-secret",
            {
                "operation": "set",
                "key": "api_key",
                "scope": "session",
                "value": "secret",
            },
        )
        assert denied.status_code == 422


def test_configuration_corruption_is_visible_to_clients(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        authorization = enroll(client, DEVICE_ONE, "configuration-corruption")
        create_session(client, authorization)
        applied = client.post(
            "/v1/configuration/mutations",
            headers=authorization,
            json=mutation(),
        )
        assert applied.status_code == 200
        asyncio.run(_corrupt(config.database_path))

        response = client.get(
            "/v1/configuration",
            headers=authorization,
            params={"session_id": "session-1"},
        )
        assert response.status_code == 503
        assert "secret" not in response.text


async def _corrupt(path) -> None:
    async with aiosqlite.connect(path) as database:
        await database.execute("UPDATE configuration_values SET value = 'credential:secret'")
        await database.commit()
