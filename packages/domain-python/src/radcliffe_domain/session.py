from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from radcliffe_domain.store import EventStore


class ConversationState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    READING = "reading"
    PAUSED = "paused"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class PlaybackStatus(StrEnum):
    STARTED = "started"
    POSITION = "position"
    COMPLETED = "completed"
    FLUSHED = "flushed"
    FAILED = "failed"


TRACE_ID_PATTERN = re.compile(r"^trc_[0-9a-f]{32}$")


def trace_id_for_operation(operation_id: str) -> str:
    return f"trc_{hashlib.sha256(operation_id.encode()).hexdigest()[:32]}"


def valid_trace_id(trace_id: str) -> bool:
    return TRACE_ID_PATTERN.fullmatch(trace_id) is not None


_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.IDLE: frozenset({ConversationState.CONNECTING}),
    ConversationState.CONNECTING: frozenset(
        {ConversationState.LISTENING, ConversationState.FAILED, ConversationState.IDLE}
    ),
    ConversationState.LISTENING: frozenset(
        {
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.PAUSED,
            ConversationState.RECONNECTING,
            ConversationState.FAILED,
            ConversationState.IDLE,
        }
    ),
    ConversationState.THINKING: frozenset(
        {
            ConversationState.SPEAKING,
            ConversationState.READING,
            ConversationState.LISTENING,
            ConversationState.RECONNECTING,
            ConversationState.FAILED,
            ConversationState.IDLE,
        }
    ),
    ConversationState.SPEAKING: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.PAUSED,
            ConversationState.RECONNECTING,
            ConversationState.FAILED,
            ConversationState.IDLE,
        }
    ),
    ConversationState.READING: frozenset(
        {
            ConversationState.LISTENING,
            ConversationState.PAUSED,
            ConversationState.RECONNECTING,
            ConversationState.FAILED,
            ConversationState.IDLE,
        }
    ),
    ConversationState.PAUSED: frozenset(
        {
            ConversationState.SPEAKING,
            ConversationState.READING,
            ConversationState.LISTENING,
            ConversationState.IDLE,
            ConversationState.FAILED,
        }
    ),
    ConversationState.RECONNECTING: frozenset(
        {ConversationState.LISTENING, ConversationState.FAILED, ConversationState.IDLE}
    ),
    ConversationState.FAILED: frozenset({ConversationState.CONNECTING, ConversationState.IDLE}),
}


@dataclass(frozen=True, slots=True)
class Operation:
    session_id: str
    generation: int
    operation_id: str
    cancellation_id: str
    trace_id: str = ""


@dataclass(frozen=True, slots=True)
class PlaybackAcknowledgement:
    acknowledgement_id: str
    operation: Operation
    segment_id: str
    status: PlaybackStatus
    position_ms: int
    boundary_index: int
    occurred_at: datetime
    detail: str = ""
    playback_id: str = ""


class InvalidTransitionError(ValueError):
    pass


class StaleOperationError(ValueError):
    pass


class SessionEngine:
    def __init__(self, session_id: str, store: EventStore) -> None:
        self.session_id = session_id
        self.store = store
        self.state = ConversationState.IDLE
        self.generation = 0
        self._resume_state: ConversationState | None = None

    def transition(self, target: ConversationState) -> None:
        if target not in _TRANSITIONS[self.state]:
            raise InvalidTransitionError(f"cannot transition from {self.state} to {target}")
        self.state = target

    def connect(self) -> None:
        self.generation += 1
        self.transition(ConversationState.CONNECTING)

    def ready(self) -> None:
        self.transition(ConversationState.LISTENING)

    def begin(self, target: ConversationState) -> Operation:
        if target not in {
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.READING,
        }:
            raise InvalidTransitionError(f"cannot begin an operation in {target}")
        self.transition(target)
        return Operation(
            session_id=self.session_id,
            generation=self.generation,
            operation_id=str(uuid4()),
            cancellation_id=str(uuid4()),
            trace_id=f"trc_{uuid4().hex}",
        )

    def assert_current(self, operation: Operation) -> None:
        if operation.session_id != self.session_id or operation.generation != self.generation:
            raise StaleOperationError("operation belongs to a stale session generation")

    def interrupt(self) -> None:
        if self.state not in {
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.READING,
            ConversationState.PAUSED,
        }:
            raise InvalidTransitionError(f"cannot interrupt from {self.state}")
        self.generation += 1
        self.state = ConversationState.LISTENING
        self._resume_state = None

    def pause(self) -> None:
        if self.state not in {
            ConversationState.LISTENING,
            ConversationState.SPEAKING,
            ConversationState.READING,
        }:
            raise InvalidTransitionError(f"cannot pause from {self.state}")
        self._resume_state = self.state
        self.transition(ConversationState.PAUSED)

    def resume(self) -> ConversationState:
        if self.state is not ConversationState.PAUSED or self._resume_state is None:
            raise InvalidTransitionError("session is not resumable")
        target = self._resume_state
        self.transition(target)
        self._resume_state = None
        return target

    def end(self) -> None:
        self.generation += 1
        if self.state is ConversationState.IDLE:
            return
        self.state = ConversationState.IDLE
        self._resume_state = None

    async def acknowledge(self, ack: PlaybackAcknowledgement) -> bool:
        self.assert_current(ack.operation)
        if ack.position_ms < 0 or ack.boundary_index < 0:
            raise ValueError("playback positions cannot be negative")
        return await self.store.record_playback(
            acknowledgement_id=ack.acknowledgement_id,
            session_id=self.session_id,
            generation=ack.operation.generation,
            operation_id=ack.operation.operation_id,
            segment_id=ack.segment_id,
            status=ack.status.value,
            position_ms=ack.position_ms,
            boundary_index=ack.boundary_index,
            occurred_at=ack.occurred_at.astimezone(UTC),
            detail=ack.detail,
            playback_id=ack.playback_id,
        )
