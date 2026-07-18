import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from radcliffe_domain.session import Operation
from radcliffe_domain.telemetry import TelemetrySink, session_pseudonym

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SPEC = spec_from_file_location("radcliffe_telemetry_under_test", SCRIPTS / "radcliffe_telemetry.py")
assert SPEC is not None and SPEC.loader is not None
TELEMETRY = module_from_spec(SPEC)
sys.modules[SPEC.name] = TELEMETRY
SPEC.loader.exec_module(TELEMETRY)


def operation(index: int) -> Operation:
    return Operation(
        session_id=f"private-session-{index}",
        generation=index,
        operation_id=f"operation-{index:016d}",
        cancellation_id=f"cancellation-{index}",
        trace_id=f"trc_{index:032x}",
    )


def emit_trace(sink: TelemetrySink, item: Operation, disposition: str) -> None:
    common = {
        "session_id": item.session_id,
        "generation": item.generation,
        "operation": item,
    }
    sink.emit(
        component="media_worker",
        event="turn",
        outcome="started",
        phase="orchestration",
        **common,
    )
    sink.emit(
        component="sidecar",
        event="active_work",
        outcome="started",
        phase="provider",
        **common,
    )
    if disposition == "failed":
        sink.emit(
            component="sidecar",
            event="provider_error",
            outcome="failed",
            phase="provider",
            duration_ms=9,
            **common,
        )
    else:
        sink.emit(
            component="media_worker",
            event="phase",
            outcome="succeeded",
            phase="first_audio",
            duration_ms=42,
            **common,
        )
        sink.emit(
            component="android",
            event="playback",
            outcome="interrupted" if disposition == "interrupted" else "completed",
            phase="acknowledgement",
            **common,
        )
    sink.emit(
        component="sidecar",
        event="active_work",
        outcome=disposition,
        phase="provider",
        duration_ms=50,
        **common,
    )
    sink.emit(
        component="media_worker",
        event="turn",
        outcome=disposition,
        phase="orchestration",
        duration_ms=55,
        **common,
    )


def test_cross_service_traces_reconstruct_without_content_or_unbounded_labels(tmp_path) -> None:
    path = tmp_path / "telemetry/events.jsonl"
    sink = TelemetrySink(path, strict=True)
    emit_trace(sink, operation(1), "completed")
    emit_trace(sink, operation(2), "interrupted")
    emit_trace(sink, operation(3), "failed")
    control = operation(4)
    sink.emit(
        component="control",
        event="tool_error",
        outcome="failed",
        session_id=control.session_id,
        generation=control.generation,
        operation=control,
        phase="authorization",
    )
    sink.emit(
        component="media_worker",
        event="reconnect",
        outcome="reconnected",
        session_id="private-reconnect-session",
        generation=1,
        phase="session",
    )
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"message": "must be dropped", "token": "secret"}) + "\n")

    events, invalid = TELEMETRY.read_events(path)
    summaries = TELEMETRY.trace_summaries(events)
    metrics = TELEMETRY.aggregate_metrics(events)
    encoded = path.read_text()

    assert invalid == 1
    assert {item["disposition"] for item in summaries} >= {
        "completed",
        "interrupted",
        "failed",
    }
    assert metrics["coverage"] == {
        "readiness": 0,
        "first_audio": 2,
        "interruption": 3,
        "reconnect": 1,
        "provider_error": 1,
        "tool_error": 1,
        "active_work": 6,
        "resource_pressure": 0,
    }
    assert metrics["active_work"] == 0
    assert "private-session" not in encoded
    assert "conversation" not in encoded
    assert path.stat().st_mode & 0o777 == 0o600
    assert session_pseudonym("private-session-1") in encoded


def test_bundle_is_private_secret_scanned_and_drops_unsafe_operational_fields(tmp_path) -> None:
    telemetry_path = tmp_path / "events.jsonl"
    sink = TelemetrySink(telemetry_path, strict=True)
    emit_trace(sink, operation(1), "completed")
    operations_path = tmp_path / "operations.json"
    operations_path.write_text(
        json.dumps(
            {
                "schema": "radcliffe-operations-status-v1",
                "created_at": "2026-07-17T00:00:00+00:00",
                "status": "ready",
                "revision": "abc123",
                "dirty": False,
                "endpoints": {"control": "http://private-host"},
                "checks": [
                    {"component": "control_readiness", "status": "ok", "code": "ready"},
                    {
                        "component": "disk",
                        "status": "warning",
                        "code": "disk_pressure",
                        "details": {"path": "/private/path"},
                    },
                ],
            }
        )
    )
    environment = {"RADCLIFFE_INTERNAL_TOKEN": "configured-secret-value"}
    bundle = TELEMETRY.build_bundle(
        telemetry_path,
        operations_path,
        environment=environment,
    )
    encoded = TELEMETRY.serialize_bundle(bundle, environment)
    destination = tmp_path / "bundle/diagnostics.json"
    TELEMETRY.write_private(destination, encoded)

    assert bundle["metrics"]["coverage"]["readiness"] == 1
    assert bundle["metrics"]["coverage"]["resource_pressure"] == 1
    assert "private-host" not in encoded
    assert "/private/path" not in encoded
    assert "configured-secret-value" not in encoded
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="configured secret"):
        TELEMETRY.serialize_bundle({"leak": "configured-secret-value"}, environment)
