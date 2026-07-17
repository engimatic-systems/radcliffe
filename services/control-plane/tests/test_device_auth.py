from __future__ import annotations

import asyncio
import base64
import json
import sqlite3

from fastapi.testclient import TestClient
from livekit import api
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings
from radcliffe_domain.devices import DeviceRegistry, credential_digest

BOOTSTRAP = "bootstrap-token-123"
INTERNAL = "internal-token-1234"
CREDENTIAL = "rdc_dev_" + "c" * 43
OTHER_CREDENTIAL = "rdc_dev_" + "d" * 43
ROTATED_CREDENTIAL = "rdc_dev_" + "e" * 43


def settings(tmp_path) -> Settings:
    source_root = tmp_path / "sources"
    source_root.mkdir(exist_ok=True)
    return Settings(
        livekit_api_key="test-key",
        livekit_api_secret="test-secret-that-is-definitely-long-enough",
        bootstrap_token=BOOTSTRAP,
        internal_token=INTERNAL,
        owner_id="test-owner",
        database_path=tmp_path / "events.sqlite3",
        source_root=source_root,
        telemetry_path=tmp_path / "telemetry.jsonl",
        room_token_ttl_seconds=60,
        managed_workers_enabled=False,
    )


def enrollment(credential: str = CREDENTIAL) -> dict[str, str]:
    return {
        "installation_id": "pixel-installation-1234",
        "display_name": "Pixel 8 Pro",
        "credential": credential,
    }


def test_enrollment_is_one_way_idempotent_and_device_identity_scopes_room_token(tmp_path) -> None:
    config = settings(tmp_path)
    bootstrap_headers = {"Authorization": f"Bearer {BOOTSTRAP}"}
    device_headers = {"Authorization": f"Bearer {CREDENTIAL}"}
    with TestClient(create_app(config)) as client:
        assert client.post("/v1/devices/enroll", json=enrollment()).status_code == 401
        first = client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json=enrollment(),
        )
        retry = client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json=enrollment(),
        )

        assert first.status_code == 200
        assert retry.status_code == 200
        assert retry.json() == first.json()
        assert "credential" not in first.json()
        assert first.json()["participant_identity"].startswith("radcliffe-dev_")
        conflict = client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json=enrollment(OTHER_CREDENTIAL),
        )
        assert conflict.status_code == 409

        assert client.get("/v1/devices/me", headers=bootstrap_headers).status_code == 401
        current = client.get("/v1/devices/me", headers=device_headers)
        assert current.status_code == 200
        assert current.json()["device_id"] == first.json()["device_id"]

        assert (
            client.post(
                "/v1/sessions",
                headers=bootstrap_headers,
                json={"participant_identity": "bootstrap-cannot-join"},
            ).status_code
            == 401
        )
        session = client.post(
            "/v1/sessions",
            headers=device_headers,
            json={"participant_identity": "caller-cannot-choose-this"},
        )
        assert session.status_code == 200
        claims = api.TokenVerifier(config.livekit_api_key, config.livekit_api_secret).verify(
            session.json()["participant_token"]
        )
        assert claims.identity == first.json()["participant_identity"]
        assert claims.identity != "caller-cannot-choose-this"
        assert claims.video.room == session.json()["session_id"]
        assert claims.video.can_publish_sources == ["microphone"]
        encoded_claims = session.json()["participant_token"].split(".")[1]
        token_claims = json.loads(
            base64.urlsafe_b64decode(encoded_claims + "=" * (-len(encoded_claims) % 4))
        )
        assert token_claims["exp"] - token_claims["nbf"] == 60

    raw_database = config.database_path.read_bytes()
    assert CREDENTIAL.encode() not in raw_database
    assert BOOTSTRAP.encode() not in raw_database
    with sqlite3.connect(config.database_path) as database:
        stored_digest = database.execute("SELECT credential_sha256 FROM devices").fetchone()[0]
    assert stored_digest == credential_digest(CREDENTIAL)


def test_device_auth_survives_restart_and_revocation_is_immediate_and_audited(tmp_path) -> None:
    config = settings(tmp_path)
    bootstrap_headers = {"Authorization": f"Bearer {BOOTSTRAP}"}
    device_headers = {"Authorization": f"Bearer {CREDENTIAL}"}
    internal_headers = {"Authorization": f"Bearer {INTERNAL}"}
    with TestClient(create_app(config)) as client:
        enrolled = client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json=enrollment(),
        ).json()
        assert client.get("/v1/sessions", headers=device_headers).status_code == 200

    with TestClient(create_app(config)) as restarted:
        assert restarted.get("/v1/sessions", headers=device_headers).status_code == 200
        assert (
            restarted.get(
                "/v1/sessions",
                headers={"Authorization": f"Bearer {OTHER_CREDENTIAL}"},
            ).status_code
            == 401
        )
        revoked = restarted.post(
            f"/v1/devices/{enrolled['device_id']}/revoke",
            headers=internal_headers,
        )
        assert revoked.status_code == 200
        assert revoked.json()["revoked"]
        assert restarted.get("/v1/sessions", headers=device_headers).status_code == 401
        assert restarted.get("/v1/devices/me", headers=device_headers).status_code == 401
        assert (
            restarted.post(
                f"/v1/devices/{enrolled['device_id']}/revoke",
                headers=device_headers,
            ).status_code
            == 401
        )
        assert restarted.get("/v1/internal/device-auth-audit").status_code == 401
        audit_response = restarted.get(
            "/v1/internal/device-auth-audit",
            headers=internal_headers,
        )
        assert audit_response.status_code == 200
        expected_fields = {"event_id", "device_id", "event_type", "reason", "occurred_at"}
        assert all(set(item) == expected_fields for item in audit_response.json())
        assert CREDENTIAL not in audit_response.text
        assert OTHER_CREDENTIAL not in audit_response.text
        assert BOOTSTRAP not in audit_response.text

    events = asyncio.run(DeviceRegistry(config.database_path).audit_events(owner_id="test-owner"))
    assert [(item.event_type, item.reason) for item in events] == [
        ("enrolled", "new_installation"),
        ("authentication_failed", "unknown_credential"),
        ("revoked", "operator_revocation"),
        ("authentication_failed", "revoked_credential"),
        ("authentication_failed", "revoked_credential"),
    ]
    audit_text = repr(events)
    assert CREDENTIAL not in audit_text
    assert OTHER_CREDENTIAL not in audit_text
    assert BOOTSTRAP not in audit_text


def test_device_credential_rotation_is_atomic_idempotent_and_one_way(tmp_path) -> None:
    config = settings(tmp_path)
    bootstrap_headers = {"Authorization": f"Bearer {BOOTSTRAP}"}
    current_headers = {"Authorization": f"Bearer {CREDENTIAL}"}
    rotated_headers = {"Authorization": f"Bearer {ROTATED_CREDENTIAL}"}
    with TestClient(create_app(config)) as client:
        enrolled = client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json=enrollment(),
        ).json()
        assert (
            client.post(
                "/v1/devices/me/credential",
                headers=bootstrap_headers,
                json={"credential": ROTATED_CREDENTIAL},
            ).status_code
            == 401
        )
        rotated = client.post(
            "/v1/devices/me/credential",
            headers=current_headers,
            json={"credential": ROTATED_CREDENTIAL},
        )
        assert rotated.status_code == 200
        assert rotated.json()["device_id"] == enrolled["device_id"]
        assert "credential" not in rotated.json()
        assert client.get("/v1/devices/me", headers=current_headers).status_code == 401
        assert client.get("/v1/devices/me", headers=rotated_headers).status_code == 200

        confirmation = client.post(
            "/v1/devices/me/credential",
            headers=rotated_headers,
            json={"credential": ROTATED_CREDENTIAL},
        )
        assert confirmation.status_code == 200

    with TestClient(create_app(config)) as restarted:
        assert restarted.get("/v1/devices/me", headers=rotated_headers).status_code == 200
        assert restarted.get("/v1/devices/me", headers=current_headers).status_code == 401

    raw_database = config.database_path.read_bytes()
    assert CREDENTIAL.encode() not in raw_database
    assert ROTATED_CREDENTIAL.encode() not in raw_database
    events = asyncio.run(DeviceRegistry(config.database_path).audit_events(owner_id="test-owner"))
    assert sum(item.event_type == "rotated" for item in events) == 1
    assert all(CREDENTIAL not in repr(item) for item in events)
    assert all(ROTATED_CREDENTIAL not in repr(item) for item in events)


def test_rotation_rejects_a_credential_owned_by_another_installation(tmp_path) -> None:
    config = settings(tmp_path)
    bootstrap_headers = {"Authorization": f"Bearer {BOOTSTRAP}"}
    current_headers = {"Authorization": f"Bearer {CREDENTIAL}"}
    with TestClient(create_app(config)) as client:
        client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json=enrollment(),
        ).raise_for_status()
        client.post(
            "/v1/devices/enroll",
            headers=bootstrap_headers,
            json={
                "installation_id": "second-installation-1234",
                "display_name": "Second device",
                "credential": OTHER_CREDENTIAL,
            },
        ).raise_for_status()

        conflict = client.post(
            "/v1/devices/me/credential",
            headers=current_headers,
            json={"credential": OTHER_CREDENTIAL},
        )

        assert conflict.status_code == 409
        assert client.get("/v1/devices/me", headers=current_headers).status_code == 200
