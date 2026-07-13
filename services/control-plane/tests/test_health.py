from fastapi.testclient import TestClient
from radcliffe_control.app import create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
