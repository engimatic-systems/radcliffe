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
