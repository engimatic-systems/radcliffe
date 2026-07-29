from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from radcliffe_domain.session import Operation, valid_trace_id

TELEMETRY_SCHEMA = "radcliffe-content-free-telemetry-v1"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_OUTCOMES = frozenset(
    {
        "started",
        "succeeded",
        "completed",
        "interrupted",
        "cancelled",
        "failed",
        "reconnected",
        "ready",
        "warning",
    }
)
_COMPONENTS = frozenset({"android", "control", "media_worker", "sidecar"})


def session_pseudonym(session_id: str) -> str:
    digest = hashlib.sha256(f"radcliffe-session-v1\0{session_id}".encode()).hexdigest()
    return f"ses_{digest[:20]}"


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    schema: str
    occurred_at: str
    component: str
    event: str
    outcome: str
    trace_id: str
    session: str
    generation: int
    operation: str
    phase: str
    duration_ms: float | None
    queue_depth: int | None = None
    queued_duration_ms: float | None = None


def validate_event(event: TelemetryEvent) -> None:
    if event.schema != TELEMETRY_SCHEMA:
        raise ValueError("unsupported telemetry schema")
    if event.component not in _COMPONENTS:
        raise ValueError("unsupported telemetry component")
    if _NAME.fullmatch(event.event) is None or _NAME.fullmatch(event.phase) is None:
        raise ValueError("telemetry event or phase is invalid")
    if event.outcome not in _OUTCOMES:
        raise ValueError("unsupported telemetry outcome")
    if event.trace_id and not valid_trace_id(event.trace_id):
        raise ValueError("telemetry trace ID is invalid")
    if re.fullmatch(r"^ses_[0-9a-f]{20}$", event.session) is None:
        raise ValueError("telemetry session pseudonym is invalid")
    if event.generation < 0:
        raise ValueError("telemetry generation is invalid")
    if len(event.operation) > 128:
        raise ValueError("telemetry operation is too long")
    if event.duration_ms is not None and not 0 <= event.duration_ms <= 86_400_000:
        raise ValueError("telemetry duration is invalid")
    if event.queue_depth is not None and not 0 <= event.queue_depth <= 1024:
        raise ValueError("telemetry queue depth is invalid")
    if event.queued_duration_ms is not None and not 0 <= event.queued_duration_ms <= 86_400_000:
        raise ValueError("telemetry queued duration is invalid")


class TelemetrySink:
    def __init__(self, path: Path | None, *, strict: bool = False) -> None:
        self.path = path
        self.strict = strict

    @classmethod
    def from_environment(cls) -> TelemetrySink:
        configured = os.environ.get("RADCLIFFE_TELEMETRY_PATH", "var/telemetry/events.jsonl")
        return cls(Path(configured))

    def emit(
        self,
        *,
        component: str,
        event: str,
        outcome: str,
        session_id: str,
        generation: int,
        phase: str,
        operation: Operation | None = None,
        duration_ms: float | None = None,
        queue_depth: int | None = None,
        queued_duration_ms: float | None = None,
    ) -> TelemetryEvent:
        record = TelemetryEvent(
            schema=TELEMETRY_SCHEMA,
            occurred_at=datetime.now(UTC).isoformat(),
            component=component,
            event=event,
            outcome=outcome,
            trace_id=operation.trace_id if operation is not None else "",
            session=session_pseudonym(session_id),
            generation=generation,
            operation=operation.operation_id if operation is not None else "",
            phase=phase,
            duration_ms=round(duration_ms, 3) if duration_ms is not None else None,
            queue_depth=queue_depth,
            queued_duration_ms=(
                round(queued_duration_ms, 3) if queued_duration_ms is not None else None
            ),
        )
        validate_event(record)
        if self.path is None:
            return record
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            encoded = (
                json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)
            self.path.chmod(0o600)
        except OSError:
            if self.strict:
                raise
        return record
