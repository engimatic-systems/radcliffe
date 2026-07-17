#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from radcliffe_domain.telemetry import TELEMETRY_SCHEMA, TelemetryEvent, validate_event

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_SCHEMA = "radcliffe-diagnostic-bundle-v1"
_EVENT_KEYS = {
    "schema",
    "occurred_at",
    "component",
    "event",
    "outcome",
    "trace_id",
    "session",
    "generation",
    "operation",
    "phase",
    "duration_ms",
}
_OPERATION = re.compile(r"^[A-Za-z0-9_.:-]{0,128}$")


def parse_event(payload: object) -> TelemetryEvent:
    if not isinstance(payload, dict) or set(payload) != _EVENT_KEYS:
        raise ValueError("telemetry event fields are invalid")
    if not isinstance(payload["duration_ms"], int | float | type(None)):
        raise ValueError("telemetry duration type is invalid")
    event = TelemetryEvent(
        schema=str(payload["schema"]),
        occurred_at=str(payload["occurred_at"]),
        component=str(payload["component"]),
        event=str(payload["event"]),
        outcome=str(payload["outcome"]),
        trace_id=str(payload["trace_id"]),
        session=str(payload["session"]),
        generation=int(payload["generation"]),
        operation=str(payload["operation"]),
        phase=str(payload["phase"]),
        duration_ms=(float(payload["duration_ms"]) if payload["duration_ms"] is not None else None),
    )
    validate_event(event)
    datetime.fromisoformat(event.occurred_at)
    if _OPERATION.fullmatch(event.operation) is None:
        raise ValueError("telemetry operation is invalid")
    return event


def read_events(
    path: Path, *, maximum_bytes: int = 10_000_000
) -> tuple[tuple[TelemetryEvent, ...], int]:
    if not path.is_file():
        return (), 0
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > maximum_bytes:
            stream.seek(size - maximum_bytes)
            stream.readline()
        lines = stream.readlines()
    events: list[TelemetryEvent] = []
    invalid = 0
    for line in lines:
        try:
            events.append(parse_event(json.loads(line)))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            invalid += 1
    return tuple(events), invalid


def metric_key(event: TelemetryEvent) -> str:
    return f"{event.component}.{event.event}.{event.phase}.{event.outcome}"


def aggregate_metrics(
    events: Iterable[TelemetryEvent],
    operations: Mapping[str, object] | None = None,
) -> dict[str, object]:
    counts: dict[str, int] = defaultdict(int)
    durations: dict[str, list[float]] = defaultdict(list)
    coverage = {
        "readiness": 0,
        "first_audio": 0,
        "interruption": 0,
        "reconnect": 0,
        "provider_error": 0,
        "tool_error": 0,
        "active_work": 0,
        "resource_pressure": 0,
    }
    active_started = 0
    active_finished = 0
    for event in events:
        key = metric_key(event)
        counts[key] += 1
        if event.duration_ms is not None:
            durations[key].append(event.duration_ms)
        if event.event == "readiness":
            coverage["readiness"] += 1
        if event.phase == "first_audio":
            coverage["first_audio"] += 1
        if event.outcome == "interrupted":
            coverage["interruption"] += 1
        if event.event == "reconnect":
            coverage["reconnect"] += 1
        if event.event == "provider_error":
            coverage["provider_error"] += 1
        if event.event == "tool_error":
            coverage["tool_error"] += 1
        if event.event == "active_work":
            coverage["active_work"] += 1
            if event.outcome == "started":
                active_started += 1
            else:
                active_finished += 1
    if operations is not None:
        checks = operations.get("checks")
        if isinstance(checks, list):
            coverage["readiness"] += sum(
                1
                for item in checks
                if isinstance(item, dict) and item.get("component") == "control_readiness"
            )
            coverage["resource_pressure"] += sum(
                1
                for item in checks
                if isinstance(item, dict)
                and item.get("component") == "disk"
                and item.get("status") in {"warning", "failed"}
            )
    duration_summary = {
        key: {
            "count": len(values),
            "sum_ms": round(sum(values), 3),
            "maximum_ms": round(max(values), 3),
        }
        for key, values in sorted(durations.items())
    }
    return {
        "counts": dict(sorted(counts.items())),
        "durations": duration_summary,
        "coverage": coverage,
        "active_work": max(0, active_started - active_finished),
    }


def trace_summaries(events: Iterable[TelemetryEvent]) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[TelemetryEvent]] = defaultdict(list)
    for event in events:
        if event.trace_id:
            grouped[event.trace_id].append(event)
    summaries = []
    for trace_id, items in grouped.items():
        ordered = sorted(items, key=lambda item: item.occurred_at)
        outcomes = {item.outcome for item in ordered}
        if "failed" in outcomes:
            disposition = "failed"
        elif "interrupted" in outcomes:
            disposition = "interrupted"
        elif "completed" in outcomes:
            disposition = "completed"
        else:
            disposition = "incomplete"
        summaries.append(
            {
                "trace_id": trace_id,
                "session": ordered[0].session,
                "generation": ordered[0].generation,
                "operation": ordered[0].operation,
                "disposition": disposition,
                "components": sorted({item.component for item in ordered}),
                "phases": [
                    {
                        "component": item.component,
                        "event": item.event,
                        "phase": item.phase,
                        "outcome": item.outcome,
                        "duration_ms": item.duration_ms,
                    }
                    for item in ordered
                ],
            }
        )
    return tuple(sorted(summaries, key=lambda item: str(item["trace_id"])))


def safe_operations(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    checks = []
    raw_checks = payload.get("checks")
    if isinstance(raw_checks, list):
        for item in raw_checks:
            if not isinstance(item, dict):
                continue
            checks.append(
                {
                    key: str(item[key])
                    for key in ("component", "status", "code")
                    if key in item and isinstance(item[key], str)
                }
            )
    return {
        "schema": str(payload.get("schema", "unknown")),
        "created_at": str(payload.get("created_at", "unknown")),
        "status": str(payload.get("status", "unknown")),
        "revision": str(payload.get("revision", "unknown")),
        "dirty": bool(payload.get("dirty", True)),
        "checks": checks,
    }


def sensitive_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    markers = ("TOKEN", "SECRET", "API_KEY", "CREDENTIAL")
    return tuple(
        value
        for key, value in environment.items()
        if len(value) >= 8 and any(marker in key.upper() for marker in markers)
    )


def serialize_bundle(payload: Mapping[str, object], environment: Mapping[str, str]) -> str:
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if any(secret in encoded for secret in sensitive_values(environment)):
        raise RuntimeError("refusing to emit a diagnostic bundle containing a configured secret")
    return encoded + "\n"


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_bundle(
    telemetry_path: Path,
    operations_path: Path,
    *,
    environment: Mapping[str, str],
) -> dict[str, object]:
    events, invalid = read_events(telemetry_path)
    operations = safe_operations(operations_path)
    return {
        "schema": BUNDLE_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "event_schema": TELEMETRY_SCHEMA,
        "invalid_events_dropped": invalid,
        "operations": operations,
        "metrics": aggregate_metrics(events, operations),
        "traces": trace_summaries(events),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Export content-free Radcliffe diagnostics")
    result.add_argument(
        "--telemetry",
        type=Path,
        default=Path(os.environ.get("RADCLIFFE_TELEMETRY_PATH", "var/telemetry/events.jsonl")),
    )
    result.add_argument(
        "--operations",
        type=Path,
        default=Path("artifacts/operations/status.json"),
    )
    result.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/diagnostics/bundle.json"),
    )
    return result


def absolute(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    arguments = parser().parse_args()
    bundle = build_bundle(
        absolute(arguments.telemetry),
        absolute(arguments.operations),
        environment=os.environ,
    )
    encoded = serialize_bundle(bundle, os.environ)
    output = absolute(arguments.output)
    write_private(output, encoded)
    traces = bundle.get("traces")
    trace_count = len(traces) if isinstance(traces, tuple | list) else 0
    print(
        json.dumps(
            {
                "status": "exported",
                "traces": trace_count,
                "invalid_events_dropped": bundle["invalid_events_dropped"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
