import json

import pytest
from fastapi.testclient import TestClient
from radcliffe_control.app import create_app
from radcliffe_control.readiness import ReadinessProbe, latest_schema_version, livekit_health_url
from radcliffe_control.settings import Settings
from radcliffe_domain.store import EventStore


def settings(tmp_path, *, managed_workers_enabled: bool = False) -> Settings:
    source_root = tmp_path / "sources"
    source_root.mkdir(exist_ok=True)
    return Settings(
        livekit_url="ws://127.0.0.1:7880",
        livekit_api_key="test-key",
        livekit_api_secret="test-secret-that-is-definitely-long-enough",
        bootstrap_token="bootstrap-token-123",
        internal_token="internal-token-1234",
        database_path=tmp_path / "events.sqlite3",
        source_root=source_root,
        telemetry_path=tmp_path / "telemetry.jsonl",
        sidecar_url="http://127.0.0.1:8787",
        managed_workers_enabled=managed_workers_enabled,
    )


def test_livekit_health_url_accepts_only_websocket_endpoints() -> None:
    assert livekit_health_url("ws://127.0.0.1:7880/rtc?token=secret") == ("http://127.0.0.1:7880/")
    assert livekit_health_url("wss://livekit.example.com") == "https://livekit.example.com/"
    assert livekit_health_url("ws://[::1]:7880/rtc") == "http://[::1]:7880/"
    assert livekit_health_url("ws://localhost:not-a-port") is None
    assert livekit_health_url("https://livekit.example.com") is None


@pytest.mark.asyncio
async def test_readiness_checks_schema_dependencies_source_and_disabled_workers(tmp_path) -> None:
    config = settings(tmp_path)
    await EventStore(config.database_path).migrate()
    requests: list[tuple[str, bool]] = []

    async def healthy(url: str, require_json_status: bool) -> bool:
        requests.append((url, require_json_status))
        return True

    report = await ReadinessProbe(config, http_probe=healthy).check(
        worker_supervisor_available=False,
        worker_maintenance_running=False,
    )

    assert report.status == "ready"
    assert [(check.component, check.status, check.code) for check in report.checks] == [
        ("database", "ok", "schema_current"),
        ("sidecar", "ok", "sidecar_ready"),
        ("livekit", "ok", "signal_reachable"),
        ("source_root", "ok", "source_root_readable"),
        ("worker_supervisor", "disabled", "managed_workers_disabled"),
        ("job_executor", "disabled", "job_executor_disabled"),
    ]
    assert report.checks[0].observed == latest_schema_version()
    assert requests == [
        ("http://127.0.0.1:8787/healthz", True),
        ("http://127.0.0.1:7880/", False),
    ]


@pytest.mark.asyncio
async def test_readiness_reports_stable_codes_without_paths_or_exception_text(tmp_path) -> None:
    config = settings(tmp_path, managed_workers_enabled=True)

    async def unavailable(_url: str, _require_json_status: bool) -> bool:
        return False

    report = await ReadinessProbe(config, http_probe=unavailable).check(
        worker_supervisor_available=False,
        worker_maintenance_running=False,
    )
    encoded = json.dumps(
        [
            {
                "component": check.component,
                "status": check.status,
                "code": check.code,
            }
            for check in report.checks
        ]
    )

    assert report.status == "not_ready"
    assert "database_missing" in encoded
    assert "sidecar_unavailable" in encoded
    assert "signal_unavailable" in encoded
    assert "supervisor_unavailable" in encoded
    assert str(tmp_path) not in encoded
    assert config.livekit_api_secret not in encoded


def test_ready_endpoint_reports_dependency_failure_without_exposing_settings(tmp_path) -> None:
    config = settings(tmp_path)

    async def healthy(_url: str, _require_json_status: bool) -> bool:
        return True

    app = create_app(config, readiness_probe=ReadinessProbe(config, http_probe=healthy))
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["checks"][0]["observed"] == latest_schema_version()
        assert config.livekit_api_secret not in ready.text
        assert str(config.database_path) not in ready.text

    async def unavailable(_url: str, _require_json_status: bool) -> bool:
        return False

    with TestClient(
        create_app(config, readiness_probe=ReadinessProbe(config, http_probe=unavailable))
    ) as client:
        blocked = client.get("/readyz")
        assert blocked.status_code == 503
        assert blocked.json()["status"] == "not_ready"
        assert {item["component"] for item in blocked.json()["checks"]} == {
            "database",
            "sidecar",
            "livekit",
            "source_root",
            "worker_supervisor",
            "job_executor",
        }


@pytest.mark.asyncio
async def test_enabled_job_executor_is_a_required_readiness_component(tmp_path) -> None:
    config = settings(tmp_path).model_copy(update={"job_executor_enabled": True})
    await EventStore(config.database_path).migrate()

    async def healthy(_url: str, _require_json_status: bool) -> bool:
        return True

    stopped = await ReadinessProbe(config, http_probe=healthy).check(
        worker_supervisor_available=False,
        worker_maintenance_running=False,
        job_executor_available=True,
        job_executor_running=False,
    )
    assert stopped.status == "not_ready"
    assert stopped.checks[-1].code == "job_executor_stopped"

    running = await ReadinessProbe(config, http_probe=healthy).check(
        worker_supervisor_available=False,
        worker_maintenance_running=False,
        job_executor_available=True,
        job_executor_running=True,
    )
    assert running.status == "ready"
    assert running.checks[-1].code == "job_executor_running"
