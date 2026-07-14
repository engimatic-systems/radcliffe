from fastapi.testclient import TestClient
from livekit import api
from radcliffe_control.app import create_app
from radcliffe_control.settings import Settings

AUTHORIZATION = {"Authorization": "Bearer bootstrap-token-123"}


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
