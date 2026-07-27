import json
import sqlite3
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = spec_from_file_location("radcliffe_ops_under_test", SCRIPTS / "radcliffe_ops.py")
assert SPEC is not None and SPEC.loader is not None
OPS = module_from_spec(SPEC)
sys.modules[SPEC.name] = OPS
SPEC.loader.exec_module(OPS)


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "radcliffe"
    migrations = root / "packages/domain-python/src/radcliffe_domain/migrations"
    migrations.mkdir(parents=True)
    (migrations / "006_current.sql").write_text("SELECT 1;\n")
    database_path = root / "var/radcliffe.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as database:
        database.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        database.execute("INSERT INTO schema_migrations VALUES (6, 'now')")
    return root, database_path


def statuses() -> tuple[object, ...]:
    return (
        OPS.ServiceStatus(
            "sidecar",
            "works.ravd.radcliffe.sidecar",
            True,
            True,
            "running",
            101,
            0,
        ),
        OPS.ServiceStatus(
            "control",
            "works.ravd.radcliffe.control",
            True,
            True,
            "running",
            102,
            0,
        ),
        OPS.ServiceStatus(
            "compat-worker",
            "works.ravd.radcliffe.worker",
            True,
            False,
            "unloaded",
            None,
            0,
        ),
    )


def test_status_report_is_ready_content_free_and_secret_scanned(tmp_path) -> None:
    root, _database_path = workspace(tmp_path)
    environment = {
        "RADCLIFFE_CONTROL_URL": "http://127.0.0.1:8000/private?token=hidden",
        "RADCLIFFE_SIDECAR_URL": "http://127.0.0.1:8787",
        "RADCLIFFE_LIVEKIT_URL": "ws://user:password@127.0.0.1:7880/rtc?token=hidden",
        "RADCLIFFE_DATABASE_PATH": "var/radcliffe.sqlite3",
        "RADCLIFFE_INTERNAL_TOKEN": "internal-secret-value",
        "RADCLIFFE_OPENAI_API_KEY": "provider-secret-value",
    }

    def fetch(url: str, require_json: bool):
        assert "internal-secret-value" not in url
        assert "password" not in url
        assert "hidden" not in url
        if url.endswith("/readyz"):
            return OPS.HttpObservation(
                200,
                {
                    "status": "ready",
                    "checks": [
                        {
                            "component": "database",
                            "status": "ok",
                            "code": "schema_current",
                            "observed": 6,
                            "path": "/must/not/leak",
                        }
                    ],
                },
            )
        if require_json:
            if ":8000" in url:
                return OPS.HttpObservation(
                    200,
                    {
                        "status": "ok",
                        "build_revision": "abc123",
                        "capabilities": [
                            "background_jobs_v1",
                            "promoted_job_results_v1",
                        ],
                    },
                )
            return OPS.HttpObservation(
                200,
                {
                    "status": "ok",
                    "build_revision": "abc123",
                    "capabilities": {
                        "tools": ["list_sources", "read_source", "run_job", "manage_job"]
                    },
                    "active_turns": 0,
                    "cancelled_turns": 2,
                    "provider": "anthropic",
                    "model_id": "claude-sonnet-5",
                    "data_boundary": "cloud_anthropic",
                    "context_accounting": "anthropic_count_tokens_conservative_estimate",
                    "cloud_budget": {
                        "basis": "anthropic/claude-sonnet-5/usd-2026-07-25",
                        "limit_usd": 0.25,
                        "spent_usd": 0.01,
                        "session_count": 1,
                    },
                },
            )
        return OPS.HttpObservation(200, None)

    report = OPS.collect_report(
        root=root,
        environment=environment,
        statuses=statuses(),
        http_fetcher=fetch,
        revision="abc123",
        dirty=False,
        versions={"python": "3.12"},
    )
    encoded = OPS.serialize_report(report, environment)

    assert report["status"] == "ready"
    assert report["endpoints"] == {
        "control": "http://127.0.0.1:8000",
        "sidecar": "http://127.0.0.1:8787",
        "livekit": "ws://127.0.0.1:7880",
    }
    assert "internal-secret-value" not in encoded
    assert "provider-secret-value" not in encoded
    assert "password" not in encoded
    assert "/must/not/leak" not in encoded
    assert str(root) not in encoded
    assert "schema_current" in encoded
    assert "active_turns" in encoded
    assert "claude-sonnet-5" in encoded
    assert "cloud_budget" in encoded
    assert "SERVICE_COMPAT" not in encoded


def test_dependency_and_database_failures_use_stable_codes(tmp_path) -> None:
    root, database_path = workspace(tmp_path)
    database_path.write_bytes(b"not a sqlite database")

    def unavailable(_url: str, _require_json: bool):
        return OPS.HttpObservation(None, None)

    report = OPS.collect_report(
        root=root,
        environment={"RADCLIFFE_DATABASE_PATH": "var/radcliffe.sqlite3"},
        statuses=statuses(),
        http_fetcher=unavailable,
    )
    codes = {item["code"] for item in report["checks"]}

    assert report["status"] == "not_ready"
    assert "database_unavailable" in codes
    assert "control_liveness_unavailable" in codes
    assert "dependencies_not_ready" in codes
    assert "sidecar_unavailable" in codes
    assert "livekit_unavailable" in codes


def test_private_report_write_is_atomic_and_owner_only(tmp_path) -> None:
    destination = tmp_path / "reports/status.json"
    OPS.write_private(destination, json.dumps({"status": "ready"}) + "\n")

    assert json.loads(destination.read_text()) == {"status": "ready"}
    assert destination.stat().st_mode & 0o777 == 0o600
