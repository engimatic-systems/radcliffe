from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Protocol, cast
from uuid import uuid4

from google.protobuf.message import DecodeError
from loguru import logger
from radcliffe_domain.configuration import (
    SETTING_DEFINITIONS,
    ConfigurationContext,
    ConfigurationKey,
    ConfigurationRegistry,
)
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.projection import (
    BACKGROUND_JOB_ANNOUNCEMENT_EVENT,
    BACKGROUND_JOB_OUTCOME_EVENT,
    BACKGROUND_JOB_REFERENCE_EVENT,
    CONFIGURATION_EVENT,
    PLAYBACK_BOUNDARY_INDEX_METADATA,
    PLAYBACK_COMPLETE_METADATA,
    PROVIDER_BUDGET_EVENT,
    SESSION_TRANSITION_EVENT,
    SOURCE_CONTENT_SHA256_METADATA,
    SOURCE_MESSAGE_ID_METADATA,
    SOURCE_PLAYBACK_EVENT,
    TOOL_FAILURE_EVENT,
    TURN_DISPOSITION_EVENT,
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
    RenderSegment,
    SpeechDelivery,
    context_replay,
    render_projection,
    resume_boundary,
)
from radcliffe_domain.session import (
    ConversationState,
    Operation,
    PlaybackAcknowledgement,
    PlaybackStatus,
    SessionEngine,
    StaleOperationError,
)
from radcliffe_domain.store import EventStore, PlaybackAttemptRecord
from radcliffe_domain.telemetry import TelemetrySink, session_pseudonym
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_media.sidecar import SidecarEvent

_SENTENCE = re.compile(r".+?[.!?](?:[\"')\]]*)\s+", re.DOTALL)
_SOURCE_CHUNK_CHARS = 320
_SOURCE_CLAUSE_BOUNDARY = re.compile(r"(?:\n+|[,:;—–-]\s+)")
_CLARIFICATION_TEXT = "I didn't catch that clearly. Please say it again."
_REGISTERED_TOOL_NAMES = frozenset(
    {
        "list_sources",
        "read_source",
        "read_job_source",
        "configure",
        "manage_session",
        "run_job",
        "manage_job",
    }
)
_SPOKEN_OUTPUT_CATEGORIES = frozenset(
    {
        "hash",
        "identifier",
        "url",
        "path",
        "timestamp",
        "stack_trace",
        "serialized_data",
        "long_number",
        "code",
        "citation",
        "repeated_punctuation",
        "acronym_metadata",
    }
)
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")

_JOB_OUTCOME_EXPLANATIONS = {
    "completed": "A promoted result may be available through manage_job status.",
    "executor_failed": "The trusted job executor stopped before it produced a result.",
    "budget_exhausted": "The job exhausted its approved model budget before producing a result.",
    "runtime_exhausted": "The job exceeded its runtime limit before producing a result.",
    "resource_exhausted": "The job exhausted a bounded resource before producing a result.",
    "step_limit_exhausted": "The job agent exhausted its allowed steps without producing a result.",
    "policy_denied": "The job was blocked by the sandbox policy.",
    "source_unavailable": "The requested source could not be acquired in the job sandbox.",
    "task_failed": "The job agent could not complete the requested objective.",
    "tool_arguments_invalid": "The job agent repeatedly produced invalid tool arguments.",
    "model_no_action": "The job model repeatedly failed to choose an available action.",
    "model_unavailable": "No approved background job model was configured.",
    "model_rate_limited": (
        "The job model provider kept rate-limiting the request after bounded retries."
    ),
    "model_provider_unavailable": (
        "The job model provider remained unavailable after bounded retries."
    ),
    "model_request_failed": "The job model provider rejected the request.",
    "model_response_invalid": "The job model provider returned an invalid or oversized response.",
}

_TOOL_FAILURE_MESSAGES = {
    "authorization_denied": "Tool call failed because permission was denied.",
    "not_found": "Tool call failed because the requested resource was not found.",
    "conflict": "Tool call failed because the operation conflicts with the current state.",
    "invalid_request": "Tool call failed because the request was rejected.",
    "gateway_unavailable": "Tool call failed because its service was unavailable.",
    "execution_failed": "Tool call failed.",
}


class JobAnnouncement(StrEnum):
    COMPLETE = "Your background job is complete."
    FAILED = "A background job failed."
    WAITING_FOR_USER = "A background job needs your input."
    WAITING_FOR_RESOURCE = "A background job is waiting for a resource."
    CANCELLED = "A background job was cancelled."


class NotificationAnnouncementOutcome(StrEnum):
    DEFERRED = "deferred"
    ALREADY_DELIVERED = "already_delivered"
    SPOKEN = "spoken"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


def _is_emoji_base(character: str) -> bool:
    codepoint = ord(character)
    return 0x1F000 <= codepoint <= 0x1FAFF or 0x2600 <= codepoint <= 0x27BF


def _canonical_model_text(value: str) -> str:
    canonical: list[str] = []
    removed_emoji = False
    for index, character in enumerate(value):
        codepoint = ord(character)
        next_is_emoji = index + 1 < len(value) and _is_emoji_base(value[index + 1])
        if _is_emoji_base(character):
            removed_emoji = True
            continue
        if codepoint == 0xFE0F and removed_emoji:
            continue
        if codepoint == 0x200D and (removed_emoji or next_is_emoji):
            removed_emoji = True
            continue
        if codepoint == 0x20E3 and canonical and canonical[-1] in "#*0123456789":
            removed_emoji = False
            continue
        canonical.append(character)
        removed_emoji = False
    return "".join(canonical)


@dataclass(slots=True)
class _ActiveTurn:
    turn_id: str
    task: asyncio.Task[None]
    cancelled: asyncio.Event
    requested_disposition: str = "interrupted"
    operation: Operation | None = None
    model_text: str = ""
    rendered_model_text: str = ""
    rendered_display_text: str = ""
    model_persisted: bool = False
    next_model_boundary: int = 0
    model_playback_segments: list[_ModelPlaybackSegment] = field(default_factory=list)
    started_at: float = field(default_factory=monotonic)
    first_model_delta_at: float | None = None
    first_audio_at: float | None = None
    source_envelope: MessageEnvelope | None = None
    next_source_boundary: int = 0
    model_metadata: dict[str, str] = field(default_factory=dict)
    user_message: str = ""
    tool_started: bool = False


@dataclass(frozen=True, slots=True)
class _PausedSource:
    envelope: MessageEnvelope
    turn_id: str
    operation: Operation


@dataclass(frozen=True, slots=True)
class _PreparedSpeech:
    """Audio synthesized ahead of playback, carrying the rate it was rendered at.

    Only audio is prepared early. Nothing here registers playback or advances a boundary,
    because synthesis is a pure function of text and rate while registration is not.
    """

    segment_id: str
    boundary_index: int
    canonical_text: str
    delivery: SpeechDelivery
    audio: bytes


@dataclass(frozen=True, slots=True)
class _PreparedSourceSpeech:
    """Bounded audio for one part of a stable source-render boundary."""

    source_segment_id: str
    completes_boundary: bool
    prepared: _PreparedSpeech


@dataclass(frozen=True, slots=True)
class _QueuedModelSpeech:
    """A model chunk awaiting local synthesis and ordered playback."""

    segment_id: str
    boundary_index: int
    canonical_text: str
    completed_display_text: str
    reason: str


@dataclass(frozen=True, slots=True)
class _ModelPlaybackSegment:
    segment_id: str
    boundary_index: int
    spoken_text: str
    completed_display_text: str


@dataclass(slots=True)
class _PendingPlayback:
    operation: Operation
    playback_id: str
    segment_id: str
    boundary_index: int
    duration_ms: int
    canonical_text: str
    source: SourceKind
    receiver_epoch: str
    credit_sequence: int
    attempt_sequence: int
    source_envelope: MessageEnvelope | None = None
    completes_source_boundary: bool = True
    accepted: asyncio.Event = field(default_factory=asyncio.Event)
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    retry: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False
    latest_position_ms: int = 0
    terminal_status: PlaybackStatus | None = None
    heard_character_count: int = 0
    superseded: bool = False


class Orchestrator(Protocol):
    def turn(
        self, request: dict[str, object], cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]: ...


class Synthesizer(Protocol):
    supports_speech_rate: bool

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes: ...


class AudioPublisher(Protocol):
    def duration_ms(self, pcm_audio: bytes) -> int: ...

    async def publish(
        self,
        segment_id: str,
        pcm_audio: bytes,
        *,
        wait_for_playback: bool = False,
    ) -> int: ...

    async def flush(self) -> None: ...


class VoiceEventSink(Protocol):
    async def message(self, envelope: MessageEnvelope) -> None: ...

    async def state_changed(
        self,
        previous: ConversationState,
        current: ConversationState,
        generation: int,
        reason: str,
        operation: Operation | None = None,
    ) -> None: ...

    async def playback_segment(
        self,
        operation: Operation,
        playback_id: str,
        segment_id: str,
        boundary_index: int,
        duration_ms: int,
        canonical_sha256: bytes,
        source: SourceKind,
        speech_rate_multiplier: float,
        receiver_epoch: str,
        credit_sequence: int,
        attempt_sequence: int,
    ) -> None: ...

    async def playback_reconciled(
        self,
        request_id: str,
        generation: int,
        replay_pending: bool,
        receiver_epoch: str,
        credit_sequence: int,
        outcome: protocol_pb2.PlaybackReconciliationOutcome,
    ) -> None: ...

    async def agent_control_request(
        self,
        request: protocol_pb2.AgentControlActionRequest,
    ) -> None: ...


class VoicePipeline:
    def __init__(
        self,
        session_id: str,
        store: EventStore,
        orchestrator: Orchestrator,
        synthesizer: Synthesizer,
        publisher: AudioPublisher,
        events: VoiceEventSink | None = None,
        telemetry: TelemetrySink | None = None,
        *,
        model_chunk_chars: int = 240,
        client_playback_acknowledgements: bool = False,
        playback_ack_timeout_seconds: float = 2.0,
        source_prefetch_depth: int = 2,
        defer_readiness_until_input: bool = False,
        configuration_registry: ConfigurationRegistry | None = None,
        configuration_owner_id: str = "local-owner",
        configuration_device_id: str | None = None,
    ) -> None:
        if model_chunk_chars <= 0:
            raise ValueError("model chunk size must be positive")
        if playback_ack_timeout_seconds <= 0:
            raise ValueError("playback acknowledgement timeout must be positive")
        if source_prefetch_depth <= 0:
            raise ValueError("source prefetch depth must be positive")
        self.store = store
        self.engine = SessionEngine(session_id, store)
        self.orchestrator = orchestrator
        self.synthesizer = synthesizer
        self.publisher = publisher
        self.events = events
        self.telemetry = telemetry or TelemetrySink(None)
        self.model_chunk_chars = model_chunk_chars
        self.client_playback_acknowledgements = client_playback_acknowledgements
        self.playback_ack_timeout_seconds = playback_ack_timeout_seconds
        self.source_prefetch_depth = source_prefetch_depth
        self.defer_readiness_until_input = defer_readiness_until_input
        self.configuration_registry = configuration_registry
        self.configuration_owner_id = configuration_owner_id
        self.configuration_device_id = configuration_device_id
        self._closed_for_handoff = False
        self._active_turn: _ActiveTurn | None = None
        self._coordinator = asyncio.Lock()
        self._turn_serial = asyncio.Lock()
        self._append_lock = asyncio.Lock()
        self._latest_submission = 0
        self._pending_unanswered_user_message: str | None = None
        self._paused_source: _PausedSource | None = None
        self._pending_playback: dict[str, _PendingPlayback] = {}
        self._playback_lock = asyncio.Lock()
        self._playback_ready = asyncio.Event()
        self._maximum_segment_duration_ms = 30_000
        self._receiver_epoch = ""
        self._credit_sequence = 0
        self._attempt_sequence = 0
        self._recovery_attempt: PlaybackAttemptRecord | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._reconnect_outcomes: dict[str, tuple[bool, str, int]] = {}

    async def start(self) -> None:
        self._recovery_attempt = await self.store.active_playback_attempt(self.engine.session_id)
        next_generation = await self.store.advance_voice_generation(self.engine.session_id)
        self.engine.generation = next_generation - 1
        if self._recovery_attempt is not None:
            self._attempt_sequence = self._recovery_attempt.attempt_sequence
        previous = self.engine.state
        self.engine.connect()
        await self._notify_state(previous, "session connected")
        if not self.defer_readiness_until_input:
            previous = self.engine.state
            self.engine.ready()
            await self._notify_state(previous, "session ready")

    async def participant_connected(self) -> None:
        """Re-arm a stopped persistent worker for a newly joined client."""
        async with self._coordinator:
            if self.engine.state not in {
                ConversationState.IDLE,
                ConversationState.FAILED,
            }:
                return
            previous = self.engine.state
            self.engine.connect()
            await self._notify_state(previous, "participant reconnected")
            if not self.defer_readiness_until_input:
                previous = self.engine.state
                self.engine.ready()
                await self._notify_state(previous, "session ready after reconnect")
            self.telemetry.emit(
                component="media_worker",
                event="reconnect",
                outcome="reconnected",
                session_id=self.engine.session_id,
                generation=self.engine.generation,
                phase="session",
            )

    async def input_ready(self) -> int:
        """Enter listening only after the transport can deliver microphone audio."""
        async with self._coordinator:
            current: ConversationState = self.engine.state
            if current in {
                ConversationState.IDLE,
                ConversationState.FAILED,
            }:
                previous = self.engine.state
                self.engine.connect()
                await self._notify_state(previous, "audio participant connected")
            ready_state: ConversationState = self.engine.state
            if ready_state is ConversationState.CONNECTING:
                previous = self.engine.state
                self.engine.ready()
                await self._notify_state(previous, "audio input ready")
            return self.engine.generation

    async def barge_in(
        self,
        *,
        verifier: str = "explicit_control",
        evidence_duration_ms: float | None = None,
        disposition: str = "interrupted",
    ) -> None:
        if disposition not in {"continued", "interrupted"}:
            raise ValueError("barge-in disposition must be continued or interrupted")
        turn = self._active_turn
        operation = turn.operation if turn is not None else None
        if turn is not None and turn.user_message and not turn.model_text and not turn.tool_started:
            self._pending_unanswered_user_message = turn.user_message
        if operation is not None:
            self.telemetry.emit(
                component="media_worker",
                event="barge_in",
                outcome="succeeded",
                session_id=self.engine.session_id,
                generation=operation.generation,
                phase=verifier,
                operation=operation,
                duration_ms=evidence_duration_ms,
            )
            self.telemetry.emit(
                component="media_worker",
                event="playback_flush",
                outcome="started",
                session_id=self.engine.session_id,
                generation=operation.generation,
                phase="barge_in",
                operation=operation,
            )
        await self._cancel_active(disposition, always_flush=True)
        if operation is not None:
            self.telemetry.emit(
                component="media_worker",
                event="playback_flush",
                outcome="succeeded",
                session_id=self.engine.session_id,
                generation=operation.generation,
                phase="barge_in",
                operation=operation,
            )
        if self.engine.state is ConversationState.PAUSED:
            previous = self.engine.state
            self._paused_source = None
            self.engine.interrupt()
            await self._notify_state(previous, disposition)

    async def pause(self) -> None:
        task: asyncio.Task[None] | None = None
        paused_source = False
        previous = self.engine.state
        operation: Operation | None = None
        async with self._coordinator:
            turn = self._active_turn
            if (
                self.engine.state is ConversationState.READING
                and turn is not None
                and turn.source_envelope is not None
                and turn.operation is not None
            ):
                turn.requested_disposition = "paused"
                turn.cancelled.set()
                task = turn.task
                operation = turn.operation
                self.engine.pause()
                paused_source = True
                if not task.done():
                    task.cancel()
        if paused_source:
            await self.publisher.flush()
            await self._notify_state(previous, "paused", operation)
            current = asyncio.current_task()
            if task is not None and task is not current:
                await asyncio.gather(task, return_exceptions=True)
            return
        if self.engine.state is not ConversationState.LISTENING:
            await self.barge_in()
        if self.engine.state is ConversationState.LISTENING:
            previous = self.engine.state
            self.engine.pause()
            await self._notify_state(previous, "paused")

    async def resume(self) -> None:
        if self.engine.state is ConversationState.PAUSED:
            previous = self.engine.state
            target = self.engine.resume()
            await self._notify_state(previous, "resumed")
            paused = self._paused_source
            if target is ConversationState.READING and paused is not None:
                task = asyncio.create_task(
                    self._resume_source(paused),
                    name="radcliffe-source-resume",
                )
                task.add_done_callback(self._consume_background_task)

    async def stop(self) -> None:
        await self._cancel_active("interrupted", always_flush=True)
        self._pending_unanswered_user_message = None
        self._paused_source = None
        previous = self.engine.state
        self.engine.end()
        if previous is not ConversationState.IDLE:
            await self._notify_state(previous, "session ended")
        else:
            await self.store.record_voice_generation(
                self.engine.session_id,
                self.engine.generation,
            )

    async def handle_transcript(self, text: str) -> None:
        await self._handle_transcript(text)

    async def submit_transcript(self, text: str) -> None:
        """Persist and activate a transcript without coupling capture to response lifetime."""

        submitted = asyncio.Event()
        task = asyncio.create_task(
            self._handle_transcript(text, submitted=submitted),
            name="radcliffe-model-turn",
        )
        task.add_done_callback(lambda _task: submitted.set())
        await submitted.wait()
        if task.done():
            await task
        else:
            task.add_done_callback(self._consume_background_task)

    async def _handle_transcript(
        self,
        text: str,
        *,
        submitted: asyncio.Event | None = None,
    ) -> None:
        if self._closed_for_handoff:
            if submitted is not None:
                submitted.set()
            return
        submission, user_message = await self._begin_user_submission(text)
        turn_id = str(uuid4())
        async with self._turn_serial:
            await self._append_text(
                SourceKind.USER,
                text,
                {TURN_ID_METADATA: turn_id},
                notify=True,
            )
            task = cast(asyncio.Task[None], asyncio.current_task())
            turn = _ActiveTurn(turn_id, task, asyncio.Event(), user_message=user_message)
            async with self._coordinator:
                if submission != self._latest_submission:
                    stale = True
                else:
                    stale = False
                    self._active_turn = turn
            if submitted is not None:
                submitted.set()
            if stale:
                await self._append_disposition(turn, "superseded")
                return
            try:
                await self._run_turn(turn)
            except asyncio.CancelledError:
                await self._finish_cancelled(turn)
            except Exception:
                await self._finish_failed(turn)
                raise
            finally:
                if submitted is not None:
                    submitted.set()
                async with self._coordinator:
                    if self._active_turn is turn:
                        self._active_turn = None

    async def request_clarification(self, reason: str) -> None:
        """Ask the user to repeat without persisting or guessing uncertain input."""
        submission = await self._begin_submission()
        turn_id = str(uuid4())
        async with self._turn_serial:
            task = cast(asyncio.Task[None], asyncio.current_task())
            turn = _ActiveTurn(
                turn_id,
                task,
                asyncio.Event(),
                model_text=_CLARIFICATION_TEXT,
                model_metadata={"radcliffe.clarification_reason": reason},
            )
            async with self._coordinator:
                if submission != self._latest_submission:
                    return
                self._active_turn = turn
            try:
                previous = self.engine.state
                operation = self.engine.begin(ConversationState.THINKING)
                turn.operation = operation
                await self._notify_state(previous, "clarification required", operation)
                await self._speak_model_chunk(
                    _CLARIFICATION_TEXT,
                    operation,
                    turn,
                    "clarification ready",
                    completed_display_text=_CLARIFICATION_TEXT,
                )
                await self._finish_completed(turn)
            except asyncio.CancelledError:
                await self._finish_cancelled(turn)
            except Exception:
                await self._finish_failed(turn)
                raise
            finally:
                async with self._coordinator:
                    if self._active_turn is turn:
                        self._active_turn = None

    def notification_foreground_available(self) -> bool:
        if (
            self.engine.state is not ConversationState.LISTENING
            or self._active_turn is not None
            or self._paused_source is not None
        ):
            return False
        return not self._current_generation_playback_pending()

    def foreground_turn_active(self) -> bool:
        return (
            self._active_turn is not None
            or self.engine.state
            in {
                ConversationState.THINKING,
                ConversationState.SPEAKING,
                ConversationState.READING,
            }
            or self._current_generation_playback_pending()
        )

    def _current_generation_playback_pending(self) -> bool:
        return any(
            pending.operation.generation == self.engine.generation
            and pending.terminal_status is None
            for pending in self._pending_playback.values()
        )

    async def announce_job(
        self,
        event_id: str,
        announcement: JobAnnouncement,
        *,
        job_id: str,
        status: str,
        detail_code: str,
        foreground_quiet: Callable[[], bool],
        claim: Callable[[], Awaitable[bool]],
        release: Callable[[], Awaitable[bool]],
    ) -> NotificationAnnouncementOutcome:
        if not isinstance(announcement, JobAnnouncement):
            raise ValueError("job announcement text must be allowlisted")
        async with self._turn_serial:
            task = cast(asyncio.Task[None], asyncio.current_task())
            turn = _ActiveTurn(
                f"job-notification:{event_id}",
                task,
                asyncio.Event(),
            )
            async with self._coordinator:
                if not self.notification_foreground_available() or not foreground_quiet():
                    return NotificationAnnouncementOutcome.DEFERRED
                self._active_turn = turn
            claimed = False
            try:
                claimed = await claim()
                if not claimed:
                    return NotificationAnnouncementOutcome.ALREADY_DELIVERED
                async with self._coordinator:
                    safe = (
                        self._active_turn is turn
                        and self.engine.state is ConversationState.LISTENING
                        and self._paused_source is None
                        and foreground_quiet()
                    )
                    if safe:
                        previous = self.engine.state
                        operation = self.engine.begin(ConversationState.SPEAKING)
                        turn.operation = operation
                    else:
                        self._active_turn = None
                if not safe:
                    await release()
                    return NotificationAnnouncementOutcome.DEFERRED
                await self._notify_state(
                    previous,
                    "background job notification ready",
                    operation,
                )
                segment_id = "job-notification:" + hashlib.sha256(event_id.encode()).hexdigest()
                await self._speak(
                    announcement.value,
                    segment_id,
                    0,
                    operation,
                    turn,
                    source=SourceKind.SYSTEM,
                    wait_for_playback=True,
                )
                await self._persist_job_announcement(
                    event_id=event_id,
                    job_id=job_id,
                    status=status,
                    detail_code=detail_code,
                    announcement=announcement,
                )
                async with self._coordinator:
                    self._assert_owner(turn, operation)
                    previous = self.engine.state
                    self.engine.transition(ConversationState.LISTENING)
                    self._active_turn = None
                    await self._notify_state(
                        previous,
                        "background job notification complete",
                        operation,
                    )
                return NotificationAnnouncementOutcome.SPOKEN
            except asyncio.CancelledError:
                await self._finish_job_announcement(turn, "interrupted")
                return (
                    NotificationAnnouncementOutcome.INTERRUPTED
                    if claimed
                    else NotificationAnnouncementOutcome.DEFERRED
                )
            except Exception as error:
                logger.error(
                    "background job announcement failed type={}",
                    type(error).__name__,
                )
                await self._finish_job_announcement(turn, "failed")
                return (
                    NotificationAnnouncementOutcome.FAILED
                    if claimed
                    else NotificationAnnouncementOutcome.DEFERRED
                )
            finally:
                async with self._coordinator:
                    if self._active_turn is turn:
                        self._active_turn = None

    async def _persist_job_announcement(
        self,
        *,
        event_id: str,
        job_id: str,
        status: str,
        detail_code: str,
        announcement: JobAnnouncement,
    ) -> None:
        if _JOB_ID.fullmatch(job_id) is None:
            raise ValueError("background job announcement has an invalid job ID")
        event_digest = hashlib.sha256(event_id.encode()).hexdigest()
        spoken_message_id = f"job-announcement:{event_digest}"
        outcome_message_id = f"job-outcome:{event_digest}"
        explanation = _JOB_OUTCOME_EXPLANATIONS.get(
            detail_code,
            "Inspect the job status before explaining this outcome.",
        )
        metadata = {
            "radcliffe.job.id": job_id,
            "radcliffe.job.status": status,
            "radcliffe.job.detail_code": detail_code,
            "radcliffe.job.event_id": event_id,
        }
        spoken: MessageEnvelope | None = None
        async with self._append_lock:
            replay = await self.store.replay(self.engine.session_id)
            existing = {item.message_id: item for item in replay}
            spoken = existing.get(spoken_message_id)
            if spoken is None:
                spoken = MessageEnvelope.text(
                    session_id=self.engine.session_id,
                    sequence=await self.store.next_sequence(self.engine.session_id),
                    source=SourceKind.SYSTEM,
                    text=announcement.value,
                    metadata={
                        **metadata,
                        TURN_EVENT_METADATA: BACKGROUND_JOB_ANNOUNCEMENT_EVENT,
                    },
                    message_id=spoken_message_id,
                )
                await self.store.append(spoken)
            if outcome_message_id not in existing:
                outcome = MessageEnvelope.text(
                    session_id=self.engine.session_id,
                    sequence=await self.store.next_sequence(self.engine.session_id),
                    source=SourceKind.CALL,
                    text=(
                        f"Internal background job outcome for {job_id}: {status}. {explanation} "
                        "Use this exact job ID with manage_job when the user asks about this job; "
                        "never quote the ID aloud."
                    ),
                    metadata={
                        **metadata,
                        TURN_EVENT_METADATA: BACKGROUND_JOB_OUTCOME_EVENT,
                    },
                    message_id=outcome_message_id,
                )
                await self.store.append(outcome)
        if self.events is not None and spoken is not None:
            await self.events.message(spoken)

    async def _finish_job_announcement(self, turn: _ActiveTurn, reason: str) -> None:
        try:
            await self.publisher.flush()
        except Exception as error:
            logger.error(
                "failed to flush a background job announcement type={}",
                type(error).__name__,
            )
        async with self._coordinator:
            if self._active_turn is not turn:
                return
            previous = self.engine.state
            if previous is ConversationState.SPEAKING:
                self.engine.interrupt()
                await self._notify_state(previous, f"background job notification {reason}")
            self._active_turn = None

    async def _run_turn(self, turn: _ActiveTurn) -> None:
        self._assert_owner(turn)
        previous = self.engine.state
        operation = self.engine.begin(ConversationState.THINKING)
        turn.operation = operation
        self.telemetry.emit(
            component="media_worker",
            event="turn",
            outcome="started",
            session_id=self.engine.session_id,
            generation=operation.generation,
            phase="orchestration",
            operation=operation,
        )
        await self._notify_state(previous, "transcription complete", operation)
        history = await self.store.replay(self.engine.session_id)
        packets = context_replay(history)
        request_id = str(uuid4())
        request: dict[str, object] = {
            "requestId": request_id,
            "sessionId": self.engine.session_id,
            "generation": self.engine.generation,
            "operationId": operation.operation_id,
            "cancellationId": operation.cancellation_id,
            "traceId": operation.trace_id,
            "userMessage": turn.user_message,
            "responseDetail": await self._effective_configuration(
                ConfigurationKey.RESPONSE_DETAIL,
                turn.turn_id,
            ),
            "packets": [
                packet.to_wire(int(item.created_at.timestamp() * 1000))
                for item, packet in zip(history, packets, strict=True)
            ],
        }
        source_rendered = False
        model_speech_queue: asyncio.Queue[_QueuedModelSpeech | None] | None = None
        model_speech_task: asyncio.Task[None] | None = None
        next_queued_model_boundary = turn.next_model_boundary

        async def finish_model_speech() -> None:
            nonlocal model_speech_queue, model_speech_task
            if model_speech_queue is None or model_speech_task is None:
                return
            queue = model_speech_queue
            task = model_speech_task
            await self._queue_model_speech(queue, None, task, allow_completion=True)
            await task
            model_speech_queue = None
            model_speech_task = None

        try:
            async for event in self.orchestrator.turn(request, turn.cancelled):
                if turn.cancelled.is_set():
                    break
                self._assert_owner(turn, operation)
                if event.type == "tool_start":
                    # Preserve the original ordering: any spoken preamble finishes before
                    # the tool transition is handled.
                    await finish_model_speech()
                    turn.tool_started = True
                elif event.type == "model_delta" and not source_rendered:
                    display_delta = _canonical_model_text(str(event.payload.get("text", "")))
                    raw_spoken = event.payload.get("spokenText")
                    spoken_delta = _canonical_model_text(
                        raw_spoken if isinstance(raw_spoken, str) else display_delta
                    )
                    if not display_delta and not spoken_delta:
                        continue
                    if turn.first_model_delta_at is None:
                        turn.first_model_delta_at = monotonic()
                        self._log_phase(
                            turn,
                            "first_model_delta",
                            turn.first_model_delta_at - turn.started_at,
                        )
                    turn.model_text += display_delta
                    raw_categories = event.payload.get("spokenCategories")
                    categories = (
                        sorted(
                            {
                                category
                                for category in raw_categories
                                if isinstance(category, str)
                                and category in _SPOKEN_OUTPUT_CATEGORIES
                            }
                        )
                        if isinstance(raw_categories, list)
                        else []
                    )
                    for category in categories:
                        self.telemetry.emit(
                            component="media_worker",
                            event="spoken_output",
                            outcome="succeeded",
                            session_id=self.engine.session_id,
                            generation=operation.generation,
                            phase=category,
                            operation=operation,
                        )
                    if categories:
                        existing = set(
                            turn.model_metadata.get("radcliffe.spoken_output.categories", "").split(
                                ","
                            )
                        )
                        existing.discard("")
                        turn.model_metadata["radcliffe.spoken_output.categories"] = ",".join(
                            sorted(existing | set(categories))
                        )
                    chunks = self._model_speech_chunks(
                        display_delta,
                        spoken_delta,
                        operation,
                        next_queued_model_boundary,
                    )
                    if chunks and model_speech_task is None:
                        model_speech_queue = asyncio.Queue(maxsize=1)
                        model_speech_task = asyncio.create_task(
                            self._speak_model_queue(
                                model_speech_queue,
                                operation,
                                turn,
                            ),
                            name="radcliffe-model-speech",
                        )
                    if model_speech_queue is not None and model_speech_task is not None:
                        for chunk in chunks:
                            await self._queue_model_speech(
                                model_speech_queue,
                                chunk,
                                model_speech_task,
                            )
                    next_queued_model_boundary += len(chunks)
                    if not self.client_playback_acknowledgements:
                        # Preserve the legacy in-process contract: handling a model delta does
                        # not return to the producer until that delta has been published.
                        await finish_model_speech()
                elif event.type == "tool_end" and bool(event.payload.get("isError")):
                    raw_tool_name = event.payload.get("toolName")
                    tool_name = (
                        raw_tool_name
                        if isinstance(raw_tool_name, str)
                        and raw_tool_name in _REGISTERED_TOOL_NAMES
                        else "unknown"
                    )
                    result = event.payload.get("result")
                    result_details = result.get("details") if isinstance(result, dict) else None
                    recoverable = (
                        isinstance(result_details, dict)
                        and result_details.get("recoverable") is True
                        and result_details.get("errorCode") == "invalid_arguments"
                    )
                    raw_error_code = (
                        result_details.get("errorCode")
                        if isinstance(result_details, dict)
                        else None
                    )
                    error_code = (
                        raw_error_code
                        if isinstance(raw_error_code, str)
                        and raw_error_code in _TOOL_FAILURE_MESSAGES
                        else "execution_failed"
                    )
                    raw_field = (
                        result_details.get("field")
                        if recoverable and isinstance(result_details, dict)
                        else None
                    )
                    field = (
                        raw_field
                        if isinstance(raw_field, str) and re.fullmatch(r"[a-z_]{1,48}", raw_field)
                        else "unknown"
                    )
                    metadata = {
                        TURN_EVENT_METADATA: TOOL_FAILURE_EVENT,
                        TURN_ID_METADATA: turn.turn_id,
                        "radcliffe.tool.name": tool_name,
                        "radcliffe.tool.outcome": "rejected" if recoverable else "failed",
                    }
                    if recoverable:
                        metadata["radcliffe.tool.error_code"] = "invalid_arguments"
                        metadata["radcliffe.tool.invalid_field"] = field
                    else:
                        metadata["radcliffe.tool.error_code"] = error_code
                    await self._append_text(
                        SourceKind.SYSTEM,
                        (
                            "Tool arguments rejected"
                            if recoverable
                            else _TOOL_FAILURE_MESSAGES[error_code]
                        ),
                        metadata,
                    )
                    self.telemetry.emit(
                        component="media_worker",
                        event="tool_call",
                        outcome="failed",
                        session_id=self.engine.session_id,
                        generation=operation.generation,
                        phase=tool_name,
                        operation=operation,
                    )
                elif event.type == "tool_end":
                    source = await self._resolve_source(event.payload.get("result"))
                    if source is not None:
                        await finish_model_speech()
                        if self.engine.state is ConversationState.THINKING:
                            await self._transition(
                                ConversationState.READING,
                                "source authorized",
                                operation,
                                turn,
                            )
                        turn.next_source_boundary = await self._start_boundary(
                            source,
                            self._requested_start_boundary(event.payload.get("result")),
                        )
                        await self._speak_plan(source, operation, turn)
                        source_rendered = True
                    else:
                        result = event.payload.get("result")
                        if event.payload.get("toolName") == "run_job":
                            await self._persist_background_job_reference(result, turn)
                        if await self._handle_session_action_tool_result(result, turn):
                            await finish_model_speech()
                            await self._finish_handoff_from_turn(turn)
                            return
                        await self._persist_configuration_update(result, turn)
                elif event.type == "model_complete" and not source_rendered:
                    await finish_model_speech()
                    turn.model_text = _canonical_model_text(
                        str(event.payload.get("text", turn.model_text))
                    )
                elif event.type == "error":
                    if event.payload.get("code") == "cloud_budget_exhausted":
                        await self._append_text(
                            SourceKind.SYSTEM,
                            "Cloud session budget exhausted",
                            {TURN_EVENT_METADATA: PROVIDER_BUDGET_EVENT},
                            notify=True,
                        )
                    raise RuntimeError(str(event.payload.get("message", "sidecar failed")))

            await finish_model_speech()
        finally:
            if model_speech_task is not None:
                model_speech_task.cancel()
                await asyncio.gather(model_speech_task, return_exceptions=True)

        self._assert_owner(turn, operation)
        await self._finish_completed(turn)

    def _model_speech_chunks(
        self,
        display_text: str,
        spoken_text: str,
        operation: Operation,
        starting_boundary: int,
    ) -> tuple[_QueuedModelSpeech, ...]:
        chunks: list[tuple[str, str]] = []
        remainder = spoken_text
        while chunk := self._next_model_chunk(remainder):
            chunks.append((chunk, "model chunk ready"))
            remainder = remainder[len(chunk) :]
        if remainder:
            chunks.append((remainder, "model response ready"))
        return tuple(
            _QueuedModelSpeech(
                segment_id=f"{operation.operation_id}:model:{starting_boundary + index}",
                boundary_index=starting_boundary + index,
                canonical_text=chunk,
                completed_display_text=display_text if index == len(chunks) - 1 else "",
                reason=reason,
            )
            for index, (chunk, reason) in enumerate(chunks)
        )

    async def _queue_model_speech(
        self,
        queue: asyncio.Queue[_QueuedModelSpeech | None],
        item: _QueuedModelSpeech | None,
        worker: asyncio.Task[None],
        *,
        allow_completion: bool = False,
    ) -> None:
        """Bound queued text while surfacing a failed speech worker without deadlocking."""

        put = asyncio.create_task(queue.put(item))
        try:
            done, _pending = await asyncio.wait(
                {put, worker},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if worker in done:
                await worker
                if not allow_completion:
                    raise RuntimeError("model speech worker stopped before the response ended")
                return
            await put
        finally:
            if not put.done():
                put.cancel()
            await asyncio.gather(put, return_exceptions=True)

    async def _prepare_model_speech(
        self,
        item: _QueuedModelSpeech,
        turn: _ActiveTurn,
    ) -> tuple[_QueuedModelSpeech, _PreparedSpeech]:
        delivery = SpeechDelivery.parse(
            await self._effective_configuration(
                ConfigurationKey.DEFAULT_SPEECH_RATE,
                turn.turn_id,
            )
        )
        audio = await self._synthesize(item.canonical_text, delivery)
        return (
            item,
            _PreparedSpeech(
                segment_id=item.segment_id,
                boundary_index=item.boundary_index,
                canonical_text=item.canonical_text,
                delivery=delivery,
                audio=audio,
            ),
        )

    async def _prepare_next_model_speech(
        self,
        queue: asyncio.Queue[_QueuedModelSpeech | None],
        turn: _ActiveTurn,
    ) -> tuple[_QueuedModelSpeech, _PreparedSpeech] | None:
        item = await queue.get()
        if item is None:
            return None
        return await self._prepare_model_speech(item, turn)

    async def _speak_model_queue(
        self,
        queue: asyncio.Queue[_QueuedModelSpeech | None],
        operation: Operation,
        turn: _ActiveTurn,
    ) -> None:
        """Keep one local audio segment ready while admitting only the current segment."""

        current = await self._prepare_next_model_speech(queue, turn)
        while current is not None:
            item, prepared = current
            # The task may wait for streaming model text or synthesize one chunk. It cannot
            # announce playback; registration remains inside _speak_model_chunk below.
            prepare_next = asyncio.create_task(
                self._prepare_next_model_speech(queue, turn),
                name="radcliffe-model-speech-lookahead",
            )
            try:
                await self._speak_model_chunk(
                    item.canonical_text,
                    operation,
                    turn,
                    item.reason,
                    completed_display_text=item.completed_display_text,
                    prepared=prepared,
                )
            except BaseException:
                prepare_next.cancel()
                await asyncio.gather(prepare_next, return_exceptions=True)
                raise
            current = await prepare_next

    def _next_model_chunk(self, buffer: str) -> str | None:
        if not buffer:
            return None
        boundaries: list[int] = []
        sentence = _SENTENCE.match(buffer)
        if sentence is not None:
            boundaries.append(sentence.end())
        for newline in re.finditer(r"\n", buffer):
            end = newline.end()
            if buffer[:end].strip():
                boundaries.append(end)
                break
        if len(buffer) >= self.model_chunk_chars:
            end = self.model_chunk_chars
            whitespace = max(
                buffer.rfind(" ", 0, end + 1),
                buffer.rfind("\t", 0, end + 1),
            )
            if whitespace >= self.model_chunk_chars // 2:
                end = whitespace + 1
            boundaries.append(end)
        return buffer[: min(boundaries)] if boundaries else None

    async def _speak_model_chunk(
        self,
        canonical_text: str,
        operation: Operation,
        turn: _ActiveTurn,
        reason: str,
        *,
        completed_display_text: str | None = None,
        prepared: _PreparedSpeech | None = None,
    ) -> None:
        if self.engine.state is ConversationState.THINKING:
            await self._transition(ConversationState.SPEAKING, reason, operation, turn)
        boundary = turn.next_model_boundary
        segment_id = f"{operation.operation_id}:model:{boundary}"
        if prepared is not None and (
            prepared.segment_id != segment_id
            or prepared.boundary_index != boundary
            or prepared.canonical_text != canonical_text
        ):
            raise RuntimeError("prefetched model audio arrived out of order")
        display_text = canonical_text if completed_display_text is None else completed_display_text
        turn.model_playback_segments.append(
            _ModelPlaybackSegment(
                segment_id,
                boundary,
                canonical_text,
                display_text,
            )
        )
        await self._speak(
            canonical_text,
            segment_id,
            boundary,
            operation,
            turn,
            source=SourceKind.MODEL,
            wait_for_playback=self.client_playback_acknowledgements,
            prepared=prepared,
        )
        turn.rendered_model_text += canonical_text
        if not self.client_playback_acknowledgements:
            turn.rendered_display_text += display_text
        turn.next_model_boundary += 1

    async def _transition(
        self,
        target: ConversationState,
        reason: str,
        operation: Operation,
        turn: _ActiveTurn,
    ) -> None:
        self._assert_owner(turn, operation)
        previous = self.engine.state
        self.engine.transition(target)
        await self._notify_state(previous, reason, operation)

    async def _notify_state(
        self,
        previous: ConversationState,
        reason: str,
        operation: Operation | None = None,
    ) -> None:
        if self.engine.generation > 0:
            await self.store.record_voice_generation(
                self.engine.session_id,
                self.engine.generation,
            )
        if self.events is not None:
            await self.events.state_changed(
                previous,
                self.engine.state,
                self.engine.generation,
                reason,
                operation,
            )

    async def _prefetch_source_audio(
        self,
        segments: Sequence[RenderSegment],
        turn: _ActiveTurn,
        queue: asyncio.Queue[_PreparedSourceSpeech | BaseException],
    ) -> None:
        """Synthesize ahead of playback. The queue's maxsize is what bounds the look-ahead."""

        try:
            for segment in segments:
                delivery = await self._segment_delivery(segment, turn)
                pending_chunks = list(
                    self._source_text_chunks(segment.canonical_text)
                    if self.client_playback_acknowledgements
                    else (segment.canonical_text,)
                )
                split = len(pending_chunks) > 1
                part_index = 0
                while pending_chunks:
                    canonical_text = pending_chunks.pop(0)
                    audio = await self._synthesize(canonical_text, delivery)
                    if self.publisher.duration_ms(audio) > self._maximum_segment_duration_ms:
                        halves = self._split_source_text(canonical_text)
                        if halves is None:
                            raise RuntimeError(
                                f"source boundary {segment.boundary_index} cannot fit "
                                "the receiver duration capacity"
                            )
                        split = True
                        pending_chunks[0:0] = halves
                        continue
                    completes_boundary = not pending_chunks
                    segment_id = (
                        f"{segment.segment_id}:part:{part_index}"
                        if split or not completes_boundary
                        else segment.segment_id
                    )
                    await queue.put(
                        _PreparedSourceSpeech(
                            source_segment_id=segment.segment_id,
                            completes_boundary=completes_boundary,
                            prepared=_PreparedSpeech(
                                segment_id=segment_id,
                                boundary_index=segment.boundary_index,
                                canonical_text=canonical_text,
                                delivery=delivery,
                                audio=audio,
                            ),
                        )
                    )
                    part_index += 1
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - handed to the reader rather than swallowed
            # Without this the reader would wait forever on a queue nobody will fill again.
            await queue.put(error)

    @staticmethod
    def _source_text_chunks(canonical_text: str) -> tuple[str, ...]:
        """Split long prose deterministically before synthesis, preserving every character."""

        chunks: list[str] = []
        remainder = canonical_text
        while len(remainder) > _SOURCE_CHUNK_CHARS:
            window = remainder[:_SOURCE_CHUNK_CHARS]
            minimum = _SOURCE_CHUNK_CHARS // 2
            clause_boundaries = [
                match.end()
                for match in _SOURCE_CLAUSE_BOUNDARY.finditer(window)
                if match.end() >= minimum
            ]
            whitespace = max(
                window.rfind(" ", minimum),
                window.rfind("\t", minimum),
            )
            boundary = (
                clause_boundaries[-1]
                if clause_boundaries
                else whitespace + 1
                if whitespace >= minimum
                else _SOURCE_CHUNK_CHARS
            )
            chunks.append(remainder[:boundary])
            remainder = remainder[boundary:]
        if remainder:
            chunks.append(remainder)
        return tuple(chunks)

    @staticmethod
    def _split_source_text(canonical_text: str) -> tuple[str, str] | None:
        """Bisect audio that still exceeds a receiver's negotiated duration."""

        if len(canonical_text) < 2:
            return None
        midpoint = len(canonical_text) // 2
        left_whitespace = max(
            canonical_text.rfind(" ", 0, midpoint + 1),
            canonical_text.rfind("\t", 0, midpoint + 1),
            canonical_text.rfind("\n", 0, midpoint + 1),
        )
        right_candidates = [
            position
            for position in (
                canonical_text.find(" ", midpoint),
                canonical_text.find("\t", midpoint),
                canonical_text.find("\n", midpoint),
            )
            if position >= 0
        ]
        right_whitespace = min(right_candidates, default=-1)
        boundaries = [
            boundary
            for boundary in (left_whitespace + 1, right_whitespace + 1)
            if 0 < boundary < len(canonical_text)
        ]
        boundary = (
            min(boundaries, key=lambda item: abs(item - midpoint)) if boundaries else midpoint
        )
        return canonical_text[:boundary], canonical_text[boundary:]

    async def _speak_plan(
        self, envelope: MessageEnvelope, operation: Operation, turn: _ActiveTurn
    ) -> None:
        plan = render_projection(envelope)
        turn.source_envelope = envelope
        remaining = [
            segment
            for segment in plan.segments
            if segment.boundary_index >= turn.next_source_boundary
        ]
        # Empty segments are never synthesized, so the look-ahead walks only what is spoken.
        spoken = [segment for segment in remaining if segment.canonical_text.strip()]
        if not spoken:
            for segment in remaining:
                turn.next_source_boundary = segment.boundary_index + 1
            return
        queue: asyncio.Queue[_PreparedSourceSpeech | BaseException] = asyncio.Queue(
            maxsize=self.source_prefetch_depth
        )
        prefetch = asyncio.create_task(self._prefetch_source_audio(spoken, turn, queue))
        try:
            for segment in remaining:
                if not segment.canonical_text.strip():
                    # Navigation and link-only lines normalize away to nothing. Advance the
                    # boundary anyway so resume positions keep lining up with the document.
                    turn.next_source_boundary = segment.boundary_index + 1
                    continue
                while True:
                    source_audio = await queue.get()
                    if isinstance(source_audio, BaseException):
                        raise source_audio
                    if (
                        source_audio.source_segment_id != segment.segment_id
                        or source_audio.prepared.boundary_index != segment.boundary_index
                    ):
                        raise RuntimeError("prefetched source audio arrived out of order")
                    prepared = source_audio.prepared
                    await self._speak(
                        prepared.canonical_text,
                        prepared.segment_id,
                        segment.boundary_index,
                        operation,
                        turn,
                        source=SourceKind.TEXT_SOURCE,
                        wait_for_playback=True,
                        source_envelope=envelope,
                        prepared=prepared,
                        completes_source_boundary=source_audio.completes_boundary,
                    )
                    if source_audio.completes_boundary:
                        break
                turn.next_source_boundary = segment.boundary_index + 1
        finally:
            # An interruption unwinds through here, discarding every sentence rendered ahead.
            # A speech rate or voice change can only reach the pipeline through such an
            # interruption, which is why the look-ahead needs no separate staleness check.
            prefetch.cancel()
            with suppress(asyncio.CancelledError):
                await prefetch

    async def _resume_source(self, paused: _PausedSource) -> None:
        task = cast(asyncio.Task[None], asyncio.current_task())
        turn = _ActiveTurn(
            turn_id=paused.turn_id,
            task=task,
            cancelled=asyncio.Event(),
            operation=paused.operation,
            source_envelope=paused.envelope,
            next_source_boundary=await self._persisted_source_boundary(paused.envelope),
        )
        async with self._coordinator:
            if self._paused_source != paused or self.engine.state is not ConversationState.READING:
                return
            self._paused_source = None
            self._active_turn = turn
        try:
            await self._speak_plan(paused.envelope, paused.operation, turn)
            await self._finish_completed(turn)
        except asyncio.CancelledError:
            await self._finish_cancelled(turn)
        except Exception:
            await self._finish_failed(turn)
            raise
        finally:
            async with self._coordinator:
                if self._active_turn is turn:
                    self._active_turn = None

    async def _persisted_source_boundary(self, envelope: MessageEnvelope) -> int:
        """Resolve how far this document was heard, across every call that read it.

        The acknowledgement ledger is keyed by segment ID, which embeds the tool call ID,
        so it only answers for the envelope that produced it. The heard ledger is keyed by
        content hash and therefore survives a fresh read_source call for the same source.
        """

        plan = render_projection(envelope)
        boundaries = [
            await self.store.playback_boundary(self.engine.session_id, segment.segment_id)
            for segment in plan.segments
        ]
        heard = resume_boundary(
            await self.store.replay(self.engine.session_id),
            envelope.content_sha256,
        )
        return min(max(*boundaries, heard, 0), len(plan.segments))

    async def _synthesize(self, canonical_text: str, delivery: SpeechDelivery) -> bytes:
        """Render text to audio. Pure with respect to playback state, so it is safe to run ahead."""

        if getattr(self.synthesizer, "supports_speech_rate", False):
            return await self.synthesizer.synthesize(canonical_text, rate=delivery.multiplier)
        if delivery.is_default:
            return await self.synthesizer.synthesize(canonical_text)
        raise RuntimeError("speech provider does not support a non-default delivery rate")

    async def _segment_delivery(self, segment: RenderSegment, turn: _ActiveTurn) -> SpeechDelivery:
        if segment.delivery_overridden:
            return segment.delivery
        return SpeechDelivery.parse(
            await self._effective_configuration(
                ConfigurationKey.DEFAULT_SPEECH_RATE,
                turn.turn_id,
            )
        )

    async def _speak(
        self,
        canonical_text: str,
        segment_id: str,
        boundary_index: int,
        operation: Operation,
        turn: _ActiveTurn,
        *,
        source: SourceKind,
        delivery: SpeechDelivery | None = None,
        wait_for_playback: bool = False,
        source_envelope: MessageEnvelope | None = None,
        prepared: _PreparedSpeech | None = None,
        completes_source_boundary: bool = True,
    ) -> None:
        self._assert_owner(turn, operation)
        synth_started = monotonic()
        if prepared is not None:
            # Already rendered by the look-ahead. Everything below this point - registration,
            # acknowledgement, the heard ledger - still happens here, in play order.
            delivery = prepared.delivery
            audio = prepared.audio
        else:
            delivery = delivery or SpeechDelivery.parse(
                await self._effective_configuration(
                    ConfigurationKey.DEFAULT_SPEECH_RATE,
                    turn.turn_id,
                )
            )
            audio = await self._synthesize(canonical_text, delivery)
        duration = self.publisher.duration_ms(audio)
        self._log_phase(turn, "synthesis", monotonic() - synth_started)
        self._assert_owner(turn, operation)
        if self.client_playback_acknowledgements and (
            duration <= 0 or duration > self._maximum_segment_duration_ms
        ):
            raise RuntimeError(f"playback segment {segment_id} exceeds receiver duration capacity")
        if self.client_playback_acknowledgements:
            await self._playback_ready.wait()
            self._assert_owner(turn, operation)

        while True:
            playback_id = str(uuid4())
            self._attempt_sequence += 1
            pending = _PendingPlayback(
                operation,
                playback_id,
                segment_id,
                boundary_index,
                duration,
                canonical_text,
                source,
                self._receiver_epoch,
                self._credit_sequence,
                self._attempt_sequence,
                source_envelope,
                completes_source_boundary,
            )
            if self.client_playback_acknowledgements:
                if self.events is None:
                    raise RuntimeError("client playback authority requires an event sink")
                async with self._playback_lock:
                    outstanding = tuple(
                        item
                        for item in self._pending_playback.values()
                        if item.operation == operation
                        and item.terminal_status is None
                        and not item.superseded
                    )
                    if outstanding:
                        raise RuntimeError("receiver playback window is exhausted")
                    await self.store.admit_playback_attempt(
                        PlaybackAttemptRecord(
                            playback_id=playback_id,
                            session_id=operation.session_id,
                            generation=operation.generation,
                            operation_id=operation.operation_id,
                            cancellation_id=operation.cancellation_id,
                            trace_id=operation.trace_id,
                            segment_id=segment_id,
                            boundary_index=boundary_index,
                            duration_ms=duration,
                            canonical_text=canonical_text,
                            source=source.value,
                            source_envelope=(
                                source_envelope.to_proto().SerializeToString(deterministic=True)
                                if source_envelope is not None
                                else None
                            ),
                            completes_source_boundary=completes_source_boundary,
                            speech_rate_multiplier=delivery.multiplier,
                            pcm_audio=audio,
                            receiver_epoch=pending.receiver_epoch,
                            credit_sequence=pending.credit_sequence,
                            attempt_sequence=pending.attempt_sequence,
                            status="announced",
                            position_ms=0,
                        )
                    )
                    self._pending_playback[playback_id] = pending
                self._emit_playback_bound(pending, "admitted")
                await self.events.playback_segment(
                    operation,
                    playback_id,
                    segment_id,
                    boundary_index,
                    duration,
                    hashlib.sha256(canonical_text.encode()).digest(),
                    source,
                    delivery.multiplier,
                    pending.receiver_epoch,
                    pending.credit_sequence,
                    pending.attempt_sequence,
                )
                accepted_wait = asyncio.create_task(pending.accepted.wait())
                retry_before_media = asyncio.create_task(pending.retry.wait())
                try:
                    try:
                        await asyncio.wait(
                            {accepted_wait, retry_before_media},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        for waiter in (accepted_wait, retry_before_media):
                            if not waiter.done():
                                waiter.cancel()
                        await asyncio.gather(
                            accepted_wait,
                            retry_before_media,
                            return_exceptions=True,
                        )
                except asyncio.CancelledError:
                    async with self._playback_lock:
                        await self.store.update_playback_attempt(
                            playback_id,
                            status="cancelled",
                            position_ms=pending.latest_position_ms,
                        )
                        self._emit_playback_bound(pending, "cancelled", retired=True)
                    raise
                if pending.retry.is_set():
                    self._assert_owner(turn, operation)
                    continue
            else:
                await self.engine.acknowledge(
                    PlaybackAcknowledgement(
                        acknowledgement_id=str(uuid4()),
                        operation=operation,
                        segment_id=segment_id,
                        status=PlaybackStatus.STARTED,
                        position_ms=0,
                        boundary_index=boundary_index,
                        occurred_at=datetime.now(UTC),
                        playback_id=playback_id,
                    )
                )
            self._assert_owner(turn, operation)
            publish_started = monotonic()
            if turn.first_audio_at is None:
                turn.first_audio_at = publish_started
                self._log_phase(turn, "first_audio", turn.first_audio_at - turn.started_at)
            try:
                published_duration = await self.publisher.publish(
                    segment_id,
                    audio,
                    wait_for_playback=(
                        False if self.client_playback_acknowledgements else wait_for_playback
                    ),
                )
            except asyncio.CancelledError:
                if self.client_playback_acknowledgements:
                    async with self._playback_lock:
                        await self.store.update_playback_attempt(
                            playback_id,
                            status="cancelled",
                            position_ms=pending.latest_position_ms,
                        )
                        self._emit_playback_bound(pending, "cancelled", retired=True)
                else:
                    await self.engine.acknowledge(
                        PlaybackAcknowledgement(
                            acknowledgement_id=str(uuid4()),
                            operation=operation,
                            segment_id=segment_id,
                            status=PlaybackStatus.FLUSHED,
                            position_ms=0,
                            boundary_index=boundary_index,
                            occurred_at=datetime.now(UTC),
                            detail="playback cancelled before boundary completion",
                            playback_id=playback_id,
                        )
                    )
                raise
            except Exception as error:
                if self.client_playback_acknowledgements:
                    async with self._playback_lock:
                        await self.store.update_playback_attempt(
                            playback_id,
                            status="failed",
                            position_ms=pending.latest_position_ms,
                        )
                        self._emit_playback_bound(pending, "failed", retired=True)
                else:
                    await self.engine.acknowledge(
                        PlaybackAcknowledgement(
                            acknowledgement_id=str(uuid4()),
                            operation=operation,
                            segment_id=segment_id,
                            status=PlaybackStatus.FAILED,
                            position_ms=0,
                            boundary_index=boundary_index,
                            occurred_at=datetime.now(UTC),
                            detail=type(error).__name__,
                            playback_id=playback_id,
                        )
                    )
                raise
            self._log_phase(turn, "publish", monotonic() - publish_started)
            self._assert_owner(turn, operation)
            if published_duration != duration:
                raise RuntimeError("published audio duration changed after playback announcement")
            if self.client_playback_acknowledgements:
                if not wait_for_playback:
                    return
                terminal_wait = asyncio.create_task(pending.terminal.wait())
                retry_wait = asyncio.create_task(pending.retry.wait())
                try:
                    await asyncio.wait(
                        {terminal_wait, retry_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for waiter in (terminal_wait, retry_wait):
                        if not waiter.done():
                            waiter.cancel()
                    await asyncio.gather(terminal_wait, retry_wait, return_exceptions=True)
                if pending.retry.is_set():
                    self._assert_owner(turn, operation)
                    continue
                if pending.terminal_status is not PlaybackStatus.COMPLETED:
                    raise asyncio.CancelledError
                return
            await self.engine.acknowledge(
                PlaybackAcknowledgement(
                    acknowledgement_id=str(uuid4()),
                    operation=operation,
                    segment_id=segment_id,
                    status=PlaybackStatus.COMPLETED,
                    position_ms=duration,
                    boundary_index=boundary_index + 1,
                    occurred_at=datetime.now(UTC),
                    playback_id=playback_id,
                )
            )
            pending.started = True
            pending.latest_position_ms = duration
            pending.terminal_status = PlaybackStatus.COMPLETED
            await self._persist_heard_source(pending, PlaybackStatus.COMPLETED)
            return

    async def playback_acknowledgement(
        self,
        acknowledgement: PlaybackAcknowledgement,
        *,
        reconciled: bool = False,
    ) -> bool:
        async with self._playback_lock:
            if not acknowledgement.playback_id:
                raise ValueError("playback acknowledgement omitted the playback attempt")
            pending = self._pending_playback.get(acknowledgement.playback_id)
            if pending is None:
                raise ValueError("playback acknowledgement refers to an unknown attempt")
            if acknowledgement.segment_id != pending.segment_id:
                raise ValueError("playback acknowledgement refers to another segment")
            if acknowledgement.operation != pending.operation:
                raise ValueError("playback acknowledgement belongs to another operation")
            if pending.superseded:
                return False
            supplied_credit_identity = bool(
                acknowledgement.receiver_epoch
                or acknowledgement.credit_sequence
                or acknowledgement.attempt_sequence
            )
            if supplied_credit_identity and (
                acknowledgement.receiver_epoch != pending.receiver_epoch
                or acknowledgement.credit_sequence != pending.credit_sequence
                or acknowledgement.attempt_sequence != pending.attempt_sequence
            ):
                raise ValueError("playback acknowledgement has stale credit identity")
            terminal_status = acknowledgement.status in {
                PlaybackStatus.COMPLETED,
                PlaybackStatus.FLUSHED,
                PlaybackStatus.FAILED,
            }
            if (
                acknowledgement.operation.session_id != self.engine.session_id
                or acknowledgement.operation.generation > self.engine.generation
                or (
                    acknowledgement.operation.generation < self.engine.generation
                    and not terminal_status
                )
            ):
                raise StaleOperationError(
                    "nonterminal playback acknowledgement belongs to a stale generation"
                )
            expected_boundary = pending.boundary_index + (
                1 if acknowledgement.status is PlaybackStatus.COMPLETED else 0
            )
            if acknowledgement.boundary_index != expected_boundary:
                raise ValueError("playback acknowledgement has an invalid boundary")
            if acknowledgement.position_ms < pending.latest_position_ms:
                raise ValueError("playback acknowledgement position moved backwards")
            if acknowledgement.position_ms > pending.duration_ms:
                raise ValueError("playback acknowledgement exceeds the announced duration")
            if pending.terminal_status is not None:
                if acknowledgement.status is not pending.terminal_status:
                    raise ValueError("playback acknowledgement followed a terminal status")
            elif acknowledgement.status is PlaybackStatus.ACCEPTED:
                if pending.accepted.is_set() or acknowledgement.position_ms != 0:
                    raise ValueError("playback acceptance acknowledgement is invalid")
                pending.accepted.set()
            elif acknowledgement.status is PlaybackStatus.STARTED:
                if (
                    not pending.accepted.is_set()
                    or pending.started
                    or acknowledgement.position_ms != 0
                ):
                    raise ValueError("playback started acknowledgement is invalid")
                pending.started = True
            elif acknowledgement.status is PlaybackStatus.POSITION:
                if not pending.started:
                    raise ValueError("playback position arrived before playback started")
            elif acknowledgement.status is PlaybackStatus.COMPLETED:
                if not pending.started and not reconciled:
                    raise ValueError("playback completion arrived before playback started")
                if acknowledgement.position_ms < pending.duration_ms:
                    raise ValueError("playback completed before the announced duration")
                pending.started = True
                pending.terminal_status = acknowledgement.status
            elif acknowledgement.status in {PlaybackStatus.FLUSHED, PlaybackStatus.FAILED}:
                pending.terminal_status = acknowledgement.status
            else:
                raise ValueError("unsupported playback acknowledgement status")
            pending.latest_position_ms = acknowledgement.position_ms
            await self.store.update_playback_attempt(
                pending.playback_id,
                status=acknowledgement.status.value,
                position_ms=acknowledgement.position_ms,
            )
            if acknowledgement.operation.generation == self.engine.generation:
                persisted = await self.engine.acknowledge(acknowledgement)
            elif (
                acknowledgement.operation.session_id == self.engine.session_id
                and acknowledgement.operation.generation < self.engine.generation
                and acknowledgement.status
                in {
                    PlaybackStatus.COMPLETED,
                    PlaybackStatus.FLUSHED,
                    PlaybackStatus.FAILED,
                }
            ):
                persisted = await self.store.record_playback(
                    acknowledgement_id=acknowledgement.acknowledgement_id,
                    session_id=acknowledgement.operation.session_id,
                    generation=acknowledgement.operation.generation,
                    operation_id=acknowledgement.operation.operation_id,
                    segment_id=acknowledgement.segment_id,
                    status=acknowledgement.status.value,
                    position_ms=acknowledgement.position_ms,
                    boundary_index=acknowledgement.boundary_index,
                    occurred_at=acknowledgement.occurred_at,
                    detail=acknowledgement.detail,
                    playback_id=acknowledgement.playback_id,
                )
            else:
                raise StaleOperationError("playback acknowledgement belongs to another generation")
            self.telemetry.emit(
                component="android",
                event="playback",
                outcome=(
                    "failed"
                    if acknowledgement.status is PlaybackStatus.FAILED
                    else "interrupted"
                    if acknowledgement.status is PlaybackStatus.FLUSHED
                    else "completed"
                    if acknowledgement.status is PlaybackStatus.COMPLETED
                    else "succeeded"
                ),
                session_id=self.engine.session_id,
                generation=acknowledgement.operation.generation,
                phase="acknowledgement",
                operation=acknowledgement.operation,
            )
            if (
                persisted
                and pending.source is SourceKind.TEXT_SOURCE
                and pending.terminal_status is not None
            ):
                await self._persist_heard_source(pending, pending.terminal_status)
            if pending.terminal_status is not None:
                pending.terminal.set()
                self._emit_playback_bound(pending, "terminal")
            return persisted

    async def reconnect(
        self,
        *,
        request_id: str,
        session_id: str,
        generation: int,
        latest_playback: tuple[PlaybackAcknowledgement, ...],
        max_inflight_attempts: int,
        max_segment_duration_ms: int,
        playback_protocol_revision: int,
        receiver_epoch: str,
        credit_sequence: int,
        idempotency_key: str,
    ) -> bool:
        if session_id != self.engine.session_id:
            raise ValueError("reconnect belongs to another session")
        if generation > self.engine.generation:
            raise StaleOperationError("reconnect generation is ahead of the worker")
        if max_inflight_attempts != 1:
            raise ValueError("bounded reconnect requires exactly one receiver attempt")
        if max_segment_duration_ms <= 0:
            raise ValueError("reconnect omitted the receiver duration capacity")
        if playback_protocol_revision != 1:
            raise ValueError("unsupported playback protocol revision")
        if not receiver_epoch or credit_sequence <= 0:
            raise ValueError("reconnect omitted the receiver credit identity")
        reconnect_key = idempotency_key or request_id
        if not reconnect_key:
            raise ValueError("reconnect omitted its idempotency identity")

        async with self._playback_lock:
            prior = self._reconnect_outcomes.get(reconnect_key)
        if prior is not None:
            prior_replay, prior_epoch, prior_sequence = prior
            if prior_epoch != receiver_epoch or prior_sequence != credit_sequence:
                raise ValueError("reconnect idempotency identity was reused")
            if self.events is not None:
                await self.events.playback_reconciled(
                    request_id,
                    self.engine.generation,
                    prior_replay,
                    prior_epoch,
                    prior_sequence,
                    (
                        protocol_pb2.PLAYBACK_RECONCILIATION_OUTCOME_REPLAY_PENDING
                        if prior_replay
                        else protocol_pb2.PLAYBACK_RECONCILIATION_OUTCOME_READY
                    ),
                )
            return prior_replay

        if receiver_epoch == self._receiver_epoch and credit_sequence <= self._credit_sequence:
            raise ValueError("reconnect supplied stale receiver credit")

        self._maximum_segment_duration_ms = min(max_segment_duration_ms, 30_000)
        self._receiver_epoch = receiver_epoch
        self._credit_sequence = credit_sequence
        self._playback_ready.set()
        await self.publisher.flush()
        snapshots = {item.playback_id: item for item in latest_playback}
        async with self._playback_lock:
            outstanding = tuple(
                item
                for item in self._pending_playback.values()
                if item.operation.session_id == session_id
                and item.operation.generation == self.engine.generation
                and item.terminal_status is None
                and not item.superseded
            )

        replay_pending = False
        for pending in outstanding:
            snapshot = snapshots.get(pending.playback_id)
            if snapshot is not None and snapshot.status in {
                PlaybackStatus.COMPLETED,
                PlaybackStatus.FLUSHED,
                PlaybackStatus.FAILED,
            }:
                await self.playback_acknowledgement(snapshot, reconciled=True)
                continue
            async with self._playback_lock:
                if pending.terminal_status is None and not pending.superseded:
                    pending.superseded = True
                    await self.store.update_playback_attempt(
                        pending.playback_id,
                        status="superseded",
                        position_ms=pending.latest_position_ms,
                    )
                    self._emit_playback_bound(pending, "superseded")
                    pending.retry.set()
                    replay_pending = True

        recovery = self._recovery_attempt
        if recovery is not None and not outstanding:
            snapshot = snapshots.get(recovery.playback_id)
            recovered_pending = self._pending_from_record(recovery)
            async with self._playback_lock:
                self._pending_playback[recovery.playback_id] = recovered_pending
            if snapshot is not None and snapshot.status in {
                PlaybackStatus.COMPLETED,
                PlaybackStatus.FLUSHED,
                PlaybackStatus.FAILED,
            }:
                await self.playback_acknowledgement(snapshot, reconciled=True)
                self._recovery_attempt = None
            else:
                recovered_pending.superseded = True
                await self.store.update_playback_attempt(
                    recovery.playback_id,
                    status="superseded",
                    position_ms=recovery.position_ms,
                )
                replay_pending = True

        async with self._playback_lock:
            self._reconnect_outcomes[reconnect_key] = (
                replay_pending,
                receiver_epoch,
                credit_sequence,
            )
            while len(self._reconnect_outcomes) > 64:
                self._reconnect_outcomes.pop(next(iter(self._reconnect_outcomes)))
        if self.events is not None:
            await self.events.playback_reconciled(
                request_id,
                self.engine.generation,
                replay_pending,
                receiver_epoch,
                credit_sequence,
                (
                    protocol_pb2.PLAYBACK_RECONCILIATION_OUTCOME_REPLAY_PENDING
                    if replay_pending
                    else protocol_pb2.PLAYBACK_RECONCILIATION_OUTCOME_READY
                ),
            )
        if (
            replay_pending
            and recovery is not None
            and self._recovery_attempt is not None
            and (self._recovery_task is None or self._recovery_task.done())
        ):
            self._recovery_task = asyncio.create_task(
                self._recover_durable_playback(recovery),
                name="radcliffe-playback-recovery",
            )
            self._recovery_task.add_done_callback(self._consume_background_task)
        return replay_pending

    def _pending_from_record(self, record: PlaybackAttemptRecord) -> _PendingPlayback:
        source_envelope = (
            MessageEnvelope.from_proto(
                protocol_pb2.MessageEnvelope.FromString(record.source_envelope)
            )
            if record.source_envelope is not None
            else None
        )
        pending = _PendingPlayback(
            operation=Operation(
                session_id=record.session_id,
                generation=record.generation,
                operation_id=record.operation_id,
                cancellation_id=record.cancellation_id,
                trace_id=record.trace_id,
            ),
            playback_id=record.playback_id,
            segment_id=record.segment_id,
            boundary_index=record.boundary_index,
            duration_ms=record.duration_ms,
            canonical_text=record.canonical_text,
            source=SourceKind(record.source),
            receiver_epoch=record.receiver_epoch,
            credit_sequence=record.credit_sequence,
            attempt_sequence=record.attempt_sequence,
            source_envelope=source_envelope,
            completes_source_boundary=record.completes_source_boundary,
            started=record.status in {"started", "position"},
            latest_position_ms=record.position_ms,
        )
        if record.status == "accepted":
            pending.accepted.set()
        return pending

    async def _recover_durable_playback(self, record: PlaybackAttemptRecord) -> None:
        if self.events is None:
            raise RuntimeError("durable playback recovery requires an event sink")
        operation = Operation(
            session_id=record.session_id,
            generation=self.engine.generation,
            operation_id=str(uuid4()),
            cancellation_id=str(uuid4()),
            trace_id=f"trc_{uuid4().hex}",
        )
        while True:
            playback_id = str(uuid4())
            self._attempt_sequence += 1
            pending = _PendingPlayback(
                operation=operation,
                playback_id=playback_id,
                segment_id=record.segment_id,
                boundary_index=record.boundary_index,
                duration_ms=record.duration_ms,
                canonical_text=record.canonical_text,
                source=SourceKind(record.source),
                receiver_epoch=self._receiver_epoch,
                credit_sequence=self._credit_sequence,
                attempt_sequence=self._attempt_sequence,
                source_envelope=(
                    MessageEnvelope.from_proto(
                        protocol_pb2.MessageEnvelope.FromString(record.source_envelope)
                    )
                    if record.source_envelope is not None
                    else None
                ),
                completes_source_boundary=record.completes_source_boundary,
            )
            await self.store.admit_playback_attempt(
                PlaybackAttemptRecord(
                    playback_id=playback_id,
                    session_id=record.session_id,
                    generation=operation.generation,
                    operation_id=operation.operation_id,
                    cancellation_id=operation.cancellation_id,
                    trace_id=operation.trace_id,
                    segment_id=record.segment_id,
                    boundary_index=record.boundary_index,
                    duration_ms=record.duration_ms,
                    canonical_text=record.canonical_text,
                    source=record.source,
                    source_envelope=record.source_envelope,
                    completes_source_boundary=record.completes_source_boundary,
                    speech_rate_multiplier=record.speech_rate_multiplier,
                    pcm_audio=record.pcm_audio,
                    receiver_epoch=pending.receiver_epoch,
                    credit_sequence=pending.credit_sequence,
                    attempt_sequence=pending.attempt_sequence,
                    status="announced",
                    position_ms=0,
                )
            )
            async with self._playback_lock:
                self._pending_playback[playback_id] = pending
            self._emit_playback_bound(pending, "recovered")
            await self.events.playback_segment(
                operation,
                playback_id,
                record.segment_id,
                record.boundary_index,
                record.duration_ms,
                hashlib.sha256(record.canonical_text.encode()).digest(),
                SourceKind(record.source),
                record.speech_rate_multiplier,
                pending.receiver_epoch,
                pending.credit_sequence,
                pending.attempt_sequence,
            )
            accepted_wait = asyncio.create_task(pending.accepted.wait())
            retry_wait = asyncio.create_task(pending.retry.wait())
            try:
                await asyncio.wait(
                    {accepted_wait, retry_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for waiter in (accepted_wait, retry_wait):
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(accepted_wait, retry_wait, return_exceptions=True)
            if pending.retry.is_set():
                continue
            await self.publisher.publish(
                record.segment_id,
                record.pcm_audio,
                wait_for_playback=False,
            )
            terminal_wait = asyncio.create_task(pending.terminal.wait())
            retry_wait = asyncio.create_task(pending.retry.wait())
            try:
                await asyncio.wait(
                    {terminal_wait, retry_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for waiter in (terminal_wait, retry_wait):
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(terminal_wait, retry_wait, return_exceptions=True)
            if pending.retry.is_set():
                continue
            if pending.terminal_status is PlaybackStatus.COMPLETED:
                await self._persist_heard_source(pending, PlaybackStatus.COMPLETED)
            self._recovery_attempt = None
            return

    def _emit_playback_bound(
        self,
        pending: _PendingPlayback,
        phase: str,
        *,
        retired: bool | None = None,
    ) -> None:
        terminal = (
            pending.terminal_status is not None or pending.superseded
            if retired is None
            else retired
        )
        self.telemetry.emit(
            component="media_worker",
            event="playback_bound",
            outcome=(
                "cancelled"
                if phase == "cancelled"
                else "failed"
                if phase == "failed"
                else "completed"
                if terminal
                else "ready"
            ),
            session_id=self.engine.session_id,
            generation=pending.operation.generation,
            phase=phase,
            operation=pending.operation,
            queue_depth=0 if terminal else 1,
            queued_duration_ms=(
                0 if terminal else max(0, pending.duration_ms - pending.latest_position_ms)
            ),
        )

    async def _persist_heard_source(
        self,
        pending: _PendingPlayback,
        status: PlaybackStatus,
    ) -> MessageEnvelope | None:
        source = pending.source_envelope
        if source is None or pending.source is not SourceKind.TEXT_SOURCE:
            return None
        acknowledged_text = (
            pending.canonical_text
            if status is PlaybackStatus.COMPLETED
            else self._acknowledged_text_prefix(
                pending.canonical_text,
                pending.latest_position_ms,
                pending.duration_ms,
            )
        )
        text = acknowledged_text[pending.heard_character_count :]
        if not text:
            return None
        metadata = {
            TURN_EVENT_METADATA: SOURCE_PLAYBACK_EVENT,
            SOURCE_MESSAGE_ID_METADATA: source.message_id,
            SOURCE_CONTENT_SHA256_METADATA: source.content_sha256.hex(),
            "radcliffe.playback.id": pending.playback_id,
            "radcliffe.playback.segment_id": pending.segment_id,
            PLAYBACK_BOUNDARY_INDEX_METADATA: str(pending.boundary_index),
            "radcliffe.playback.position_ms": str(pending.latest_position_ms),
            "radcliffe.playback.duration_ms": str(pending.duration_ms),
            "radcliffe.playback.status": status.value,
            PLAYBACK_COMPLETE_METADATA: str(
                status is PlaybackStatus.COMPLETED and pending.completes_source_boundary
            ).lower(),
        }
        async with self._append_lock:
            envelope = MessageEnvelope.text(
                session_id=self.engine.session_id,
                sequence=await self.store.next_sequence(self.engine.session_id),
                source=SourceKind.TEXT_SOURCE,
                text=text,
                metadata=metadata,
                provenance=source.provenance,
                sensitivity=source.sensitivity,
                message_id=f"heard:{pending.playback_id}:{len(acknowledged_text)}",
            )
            await self.store.append(envelope)
        pending.heard_character_count = len(acknowledged_text)
        if self.events is not None:
            await self.events.message(envelope)
        return envelope

    @staticmethod
    def _acknowledged_text_prefix(text: str, position_ms: int, duration_ms: int) -> str:
        if not text or position_ms <= 0 or duration_ms <= 0:
            return ""
        if position_ms >= duration_ms:
            return text
        character_limit = min(len(text), int(len(text) * position_ms / duration_ms))
        if character_limit <= 0:
            return ""
        candidate = text[:character_limit]
        whitespace_boundaries = [match.end() for match in re.finditer(r"\s+", candidate)]
        return text[: whitespace_boundaries[-1]] if whitespace_boundaries else ""

    async def _begin_submission(self) -> int:
        async with self._coordinator:
            self._pending_unanswered_user_message = None
            self._latest_submission += 1
            submission = self._latest_submission
        await self._cancel_active("superseded")
        return submission

    async def _begin_user_submission(self, text: str) -> tuple[int, str]:
        async with self._coordinator:
            active = self._active_turn
            pending_user_message = self._pending_unanswered_user_message
            self._pending_unanswered_user_message = None
            if (
                active is not None
                and active.user_message
                and not active.model_text
                and not active.tool_started
            ):
                user_message = active.user_message + "\n" + text
            elif pending_user_message:
                user_message = pending_user_message + "\n" + text
            else:
                user_message = text
            self._latest_submission += 1
            submission = self._latest_submission
        await self._cancel_active("superseded")
        return submission, user_message

    async def _cancel_active(self, disposition: str, *, always_flush: bool = False) -> None:
        task: asyncio.Task[None] | None = None
        recovery_task = self._recovery_task
        should_flush = always_flush
        operation: Operation | None = None
        async with self._coordinator:
            turn = self._active_turn
            if turn is not None:
                should_flush = True
                operation = turn.operation
                turn.requested_disposition = disposition
                if not turn.cancelled.is_set():
                    turn.cancelled.set()
                    task = turn.task
                    task.cancel()
        if should_flush:
            await self.publisher.flush()
        if recovery_task is not None and not recovery_task.done():
            recovery_task.cancel()
            await asyncio.gather(recovery_task, return_exceptions=True)
            async with self._playback_lock:
                recovering = tuple(
                    item
                    for item in self._pending_playback.values()
                    if item.terminal_status is None and not item.superseded
                )
                for item in recovering:
                    item.superseded = True
                    await self.store.update_playback_attempt(
                        item.playback_id,
                        status="cancelled",
                        position_ms=item.latest_position_ms,
                    )
                    self._emit_playback_bound(item, "cancelled")
            self._recovery_attempt = None
        if operation is not None:
            await self._commit_source_high_water(operation)
        current = asyncio.current_task()
        if task is not None and task is not current:
            await asyncio.gather(task, return_exceptions=True)
            await self.publisher.flush()

    async def _commit_source_high_water(self, operation: Operation) -> None:
        if not self.client_playback_acknowledgements:
            return
        async with self._playback_lock:
            pending = tuple(
                item
                for item in self._pending_playback.values()
                if item.operation == operation
                and item.source is SourceKind.TEXT_SOURCE
                and item.terminal_status is None
                and not item.superseded
            )
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(item.terminal.wait() for item in pending)),
                    timeout=min(self.playback_ack_timeout_seconds, 0.5),
                )
            except TimeoutError:
                pass
        async with self._playback_lock:
            for item in pending:
                if item.latest_position_ms <= 0:
                    continue
                await self._persist_heard_source(
                    item,
                    item.terminal_status or PlaybackStatus.POSITION,
                )

    async def _finish_completed(self, turn: _ActiveTurn) -> None:
        if turn.operation is not None:
            await self._await_operation_playback(turn.operation)
        async with self._coordinator:
            self._assert_owner(turn, turn.operation)
            await self._refresh_durable_model_prefix(turn)
            if turn.model_text and not turn.model_persisted:
                await self._persist_model(turn, notify=True)
            previous = self.engine.state
            self.engine.transition(ConversationState.LISTENING)
            await self._append_disposition(turn, "completed")
            self._active_turn = None
            self._paused_source = None
            await self._notify_state(previous, "turn complete", turn.operation)
            self._emit_turn_outcome(turn, "completed")

    async def _await_operation_playback(self, operation: Operation) -> None:
        if not self.client_playback_acknowledgements:
            return
        async with self._playback_lock:
            pending = tuple(
                item
                for item in self._pending_playback.values()
                if item.operation == operation
                and item.terminal_status is None
                and not item.superseded
            )
            remaining_ms = sum(
                max(0, item.duration_ms - item.latest_position_ms) for item in pending
            )
        if not pending:
            return
        timeout_seconds = remaining_ms / 1000 + self.playback_ack_timeout_seconds
        try:
            await asyncio.wait_for(
                asyncio.gather(*(item.terminal.wait() for item in pending)),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            raise RuntimeError("client did not acknowledge completed turn playback") from error
        async with self._playback_lock:
            terminal_statuses = tuple(item.terminal_status for item in pending)
        if PlaybackStatus.FAILED in terminal_statuses:
            raise RuntimeError("client playback failed before turn completion")
        if any(status is not PlaybackStatus.COMPLETED for status in terminal_statuses):
            raise asyncio.CancelledError

    async def _refresh_durable_model_prefix(self, turn: _ActiveTurn) -> str:
        display_parts: list[str] = []
        spoken_parts: list[str] = []
        for segment in turn.model_playback_segments:
            boundary = await self.store.playback_boundary(
                self.engine.session_id,
                segment.segment_id,
            )
            if boundary <= segment.boundary_index:
                break
            spoken_parts.append(segment.spoken_text)
            if segment.completed_display_text:
                display_parts.append(segment.completed_display_text)
        turn.rendered_model_text = "".join(spoken_parts)
        turn.rendered_display_text = "".join(display_parts)
        return turn.rendered_display_text

    async def _finish_cancelled(self, turn: _ActiveTurn) -> None:
        async with self._coordinator:
            if self._active_turn is not turn:
                return
            if (
                turn.requested_disposition == "paused"
                and turn.source_envelope is not None
                and turn.operation is not None
            ):
                self._paused_source = _PausedSource(
                    turn.source_envelope,
                    turn.turn_id,
                    turn.operation,
                )
                self._active_turn = None
                return
            self._paused_source = None
            durable_display = await self._refresh_durable_model_prefix(turn)
            if durable_display and not turn.model_persisted:
                await self._persist_model(
                    turn,
                    partial=True,
                    notify=True,
                    text=durable_display,
                )
            await self._append_disposition(turn, turn.requested_disposition)
            previous = self.engine.state
            if previous in {
                ConversationState.THINKING,
                ConversationState.SPEAKING,
                ConversationState.READING,
                ConversationState.PAUSED,
            }:
                self.engine.interrupt()
                await self._notify_state(previous, turn.requested_disposition, turn.operation)
            self._active_turn = None
            self._emit_turn_outcome(
                turn,
                "interrupted" if turn.requested_disposition == "interrupted" else "cancelled",
            )

    async def _finish_failed(self, turn: _ActiveTurn) -> None:
        async with self._coordinator:
            if self._active_turn is not turn:
                return
            try:
                await self.publisher.flush()
            except Exception as error:
                logger.error(
                    "failed to flush audio after a voice turn error type={}",
                    type(error).__name__,
                )
            durable_display = await self._refresh_durable_model_prefix(turn)
            if durable_display and not turn.model_persisted:
                await self._persist_model(
                    turn,
                    partial=True,
                    notify=True,
                    text=durable_display,
                )
            await self._append_disposition(turn, "failed")
            self._paused_source = None
            previous = self.engine.state
            if previous in {
                ConversationState.THINKING,
                ConversationState.SPEAKING,
                ConversationState.READING,
                ConversationState.PAUSED,
            }:
                self.engine.interrupt()
                await self._notify_state(previous, "turn failed", turn.operation)
            self._active_turn = None
            self._emit_turn_outcome(turn, "failed")

    async def _append_disposition(self, turn: _ActiveTurn, disposition: str) -> MessageEnvelope:
        operation = turn.operation
        metadata = {
            TURN_ID_METADATA: turn.turn_id,
            TURN_EVENT_METADATA: TURN_DISPOSITION_EVENT,
            TURN_DISPOSITION_METADATA: disposition,
        }
        if operation is not None:
            metadata["radcliffe.operation_id"] = operation.operation_id
            metadata["radcliffe.generation"] = str(operation.generation)
        return await self._append_text(
            SourceKind.SYSTEM,
            f"Turn {disposition}",
            metadata,
        )

    async def _persist_configuration_update(
        self,
        result: object,
        turn: _ActiveTurn,
    ) -> bool:
        details = self._tool_details(result)
        if details is None:
            return False
        key = self._detail_text(details, "key")
        value = self._detail_text(details, "value")
        operation = self._detail_text(details, "operation")
        if key is None or value is None or operation not in {"set", "reset", "undo"}:
            return False
        metadata = {
            TURN_ID_METADATA: turn.turn_id,
            TURN_EVENT_METADATA: CONFIGURATION_EVENT,
            "radcliffe.configuration.key": key,
            "radcliffe.configuration.value": value,
            "radcliffe.configuration.scope": (
                self._detail_text(details, "effectiveScope") or "built_in"
            ),
            "radcliffe.configuration.revision": self._detail_text(details, "revision") or "0",
            "radcliffe.configuration.mutation_id": (self._detail_text(details, "mutationId") or ""),
        }
        await self._append_text(
            SourceKind.SYSTEM,
            "Behavior setting updated",
            metadata,
            notify=True,
        )
        return True

    async def _persist_background_job_reference(
        self,
        result: object,
        turn: _ActiveTurn,
    ) -> MessageEnvelope | None:
        details = self._tool_details(result)
        if details is None:
            return None
        job_id = self._detail_text(details, "jobId")
        if job_id is None or _JOB_ID.fullmatch(job_id) is None:
            return None
        message_id = f"job-reference:{hashlib.sha256(job_id.encode()).hexdigest()}"
        async with self._append_lock:
            existing = next(
                (
                    item
                    for item in await self.store.replay(self.engine.session_id)
                    if item.message_id == message_id
                ),
                None,
            )
            if existing is not None:
                return existing
            envelope = MessageEnvelope.text(
                session_id=self.engine.session_id,
                sequence=await self.store.next_sequence(self.engine.session_id),
                source=SourceKind.CALL,
                text=(
                    f"Internal background job reference: {job_id}. "
                    "Use this exact ID with manage_job when the user refers to this job. "
                    "Never quote the ID aloud."
                ),
                metadata={
                    TURN_EVENT_METADATA: BACKGROUND_JOB_REFERENCE_EVENT,
                    "radcliffe.job.id": job_id,
                    "radcliffe.job.origin_turn_id": turn.turn_id,
                    "radcliffe.tool.name": "run_job",
                },
                message_id=message_id,
            )
            await self.store.append(envelope)
        return envelope

    async def _handle_session_action_tool_result(
        self,
        result: object,
        turn: _ActiveTurn,
    ) -> bool:
        details = self._tool_details(result)
        if details is None:
            return False
        status = self._detail_text(details, "sessionTransitionStatus")
        if status == "awaiting_confirmation":
            encoded = self._detail_text(details, "actionRequestProtoBase64")
            if encoded is None:
                raise RuntimeError("session action confirmation request is missing")
            try:
                request = protocol_pb2.AgentControlActionRequest.FromString(
                    base64.b64decode(encoded, validate=True)
                )
            except (ValueError, DecodeError) as error:
                raise RuntimeError("session action confirmation request is invalid") from error
            if (
                request.operation.session_id != self.engine.session_id
                or request.operation.generation != self.engine.generation
                or request.action_id
                not in {
                    protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_SWITCH,
                    protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_RESTART,
                    protocol_pb2.AGENT_CONTROL_ACTION_ID_SESSION_END,
                }
            ):
                raise RuntimeError("session action confirmation request is stale")
            if self.events is not None:
                await self.events.agent_control_request(request)
            return False
        if status != "applied":
            return False
        await self._persist_session_transition(result, turn.turn_id)
        return True

    async def complete_session_action(self, result: object) -> bool:
        details = self._tool_details(result)
        if details is None:
            return False
        status = self._detail_text(details, "sessionTransitionStatus")
        if status == "cancelled":
            await self._append_text(
                SourceKind.SYSTEM,
                "Conversation transition canceled",
                {
                    TURN_EVENT_METADATA: SESSION_TRANSITION_EVENT,
                    "radcliffe.session_transition.status": "cancelled",
                    "radcliffe.session_transition.request_id": (
                        self._detail_text(details, "requestId") or ""
                    ),
                },
                notify=True,
            )
            return False
        if status != "applied":
            return False
        self._closed_for_handoff = True
        await self._cancel_active("handed_off", always_flush=True)
        await self._persist_session_transition(result, None)
        if self.engine.state is not ConversationState.IDLE:
            self.engine.end()
            await self.store.record_voice_generation(
                self.engine.session_id,
                self.engine.generation,
            )
        return True

    async def _persist_session_transition(
        self,
        result: object,
        turn_id: str | None,
    ) -> None:
        details = self._tool_details(result)
        if details is None:
            raise RuntimeError("session transition result is missing")
        action = self._detail_text(details, "sessionAction")
        source = self._detail_text(details, "sourceSessionId")
        destination = self._detail_text(details, "destinationSessionId")
        request_id = self._detail_text(details, "requestId")
        if action not in {"create", "switch", "restart", "end"}:
            raise RuntimeError("session transition action is invalid")
        if source != self.engine.session_id or request_id is None:
            raise RuntimeError("session transition result is stale")
        if action != "end" and not destination:
            raise RuntimeError("session transition destination is missing")
        metadata = {
            TURN_EVENT_METADATA: SESSION_TRANSITION_EVENT,
            "radcliffe.session_transition.status": "applied",
            "radcliffe.session_transition.action": action,
            "radcliffe.session_transition.source_session_id": source,
            "radcliffe.session_transition.destination_session_id": destination or "",
            "radcliffe.session_transition.request_id": request_id,
        }
        if turn_id is not None:
            metadata[TURN_ID_METADATA] = turn_id
        await self._append_text(
            SourceKind.SYSTEM,
            "Conversation transition applied",
            metadata,
            notify=True,
        )

    async def _finish_handoff_from_turn(self, turn: _ActiveTurn) -> None:
        self._assert_owner(turn, turn.operation)
        self._closed_for_handoff = True
        turn.cancelled.set()
        await self.publisher.flush()
        await self._append_disposition(turn, "handed_off")
        self._paused_source = None
        self.engine.end()
        await self.store.record_voice_generation(
            self.engine.session_id,
            self.engine.generation,
        )
        self._active_turn = None
        self._emit_turn_outcome(turn, "completed")

    async def _persist_model(
        self,
        turn: _ActiveTurn,
        *,
        partial: bool = False,
        notify: bool = False,
        text: str | None = None,
    ) -> None:
        persisted_text = turn.model_text if text is None else text
        if turn.model_persisted or not persisted_text:
            return
        metadata = {
            TURN_ID_METADATA: turn.turn_id,
            "radcliffe.rendered_chars": str(len(turn.rendered_model_text)),
            "radcliffe.rendered_display_chars": str(len(turn.rendered_display_text)),
            **turn.model_metadata,
        }
        if turn.operation is not None:
            metadata["radcliffe.operation_id"] = turn.operation.operation_id
            metadata["radcliffe.generation"] = str(turn.operation.generation)
        if partial:
            metadata["radcliffe.partial"] = "true"
        append_task = asyncio.create_task(
            self._append_text(
                SourceKind.MODEL,
                persisted_text,
                metadata,
            )
        )
        try:
            envelope = await asyncio.shield(append_task)
        except asyncio.CancelledError:
            await append_task
            turn.model_persisted = True
            raise
        turn.model_persisted = True
        if notify and self.events is not None:
            await self.events.message(envelope)

    async def _effective_configuration(
        self,
        key: ConfigurationKey,
        turn_id: str,
    ) -> str:
        if self.configuration_registry is None:
            return SETTING_DEFINITIONS[key].default
        effective = await self.configuration_registry.inspect(
            key,
            ConfigurationContext(
                owner_id=self.configuration_owner_id,
                device_id=self.configuration_device_id,
                session_id=self.engine.session_id,
                turn_id=turn_id,
            ),
        )
        return effective.value

    def _log_phase(self, turn: _ActiveTurn, phase: str, seconds: float) -> None:
        operation = turn.operation
        logger.info(
            "voice turn phase complete phase={} duration_ms={} session_id={} "
            "operation_id={} generation={}",
            phase,
            round(seconds * 1000, 2),
            session_pseudonym(self.engine.session_id),
            operation.operation_id if operation is not None else "pending",
            operation.generation if operation is not None else self.engine.generation,
        )
        self.telemetry.emit(
            component="media_worker",
            event="phase",
            outcome="succeeded",
            session_id=self.engine.session_id,
            generation=operation.generation if operation is not None else self.engine.generation,
            phase=phase,
            operation=operation,
            duration_ms=seconds * 1000,
        )

    def _emit_turn_outcome(self, turn: _ActiveTurn, outcome: str) -> None:
        operation = turn.operation
        if operation is None:
            return
        self.telemetry.emit(
            component="media_worker",
            event="turn",
            outcome=outcome,
            session_id=self.engine.session_id,
            generation=operation.generation,
            phase="orchestration",
            operation=operation,
            duration_ms=(monotonic() - turn.started_at) * 1000,
        )

    @staticmethod
    def _consume_background_task(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("background source resume failed type={}", type(error).__name__)

    async def _append_text(
        self,
        source: SourceKind,
        text: str,
        metadata: dict[str, str] | None = None,
        *,
        notify: bool = False,
    ) -> MessageEnvelope:
        async with self._append_lock:
            envelope = MessageEnvelope.text(
                session_id=self.engine.session_id,
                sequence=await self.store.next_sequence(self.engine.session_id),
                source=source,
                text=text,
                metadata=metadata,
            )
            await self.store.append(envelope)
        if notify and self.events is not None:
            await self.events.message(envelope)
        return envelope

    def _assert_owner(self, turn: _ActiveTurn, operation: Operation | None = None) -> None:
        if self._active_turn is not turn or turn.cancelled.is_set():
            raise asyncio.CancelledError
        if operation is not None:
            self.engine.assert_current(operation)

    async def _start_boundary(
        self,
        source: MessageEnvelope,
        requested: int | None,
    ) -> int:
        """Choose where to begin reading: an explicit request, else the furthest heard point."""

        if requested is None:
            return await self._persisted_source_boundary(source)
        segments = len(render_projection(source).segments)
        if requested > segments:
            raise ValueError("requested start boundary is beyond the end of the source")
        return requested

    @staticmethod
    def _requested_start_boundary(result: object) -> int | None:
        details = VoicePipeline._tool_details(result)
        if details is None:
            return None
        raw = VoicePipeline._detail_text(details, "startAtBoundary")
        if raw is None:
            return None
        try:
            boundary = int(raw)
        except ValueError as error:
            raise ValueError("tool result carried an invalid start boundary") from error
        if boundary < 0:
            raise ValueError("tool result carried a negative start boundary")
        return boundary

    async def _resolve_source(self, result: object) -> MessageEnvelope | None:
        details = self._tool_details(result)
        if details is None:
            return None
        source_id = self._detail_text(details, "sourceMessageId")
        if source_id is None:
            return None
        encoded = self._detail_text(details, "sourceEnvelopeProtoBase64")
        if encoded is None:
            return next(
                (
                    item
                    for item in await self.store.replay(self.engine.session_id)
                    if item.message_id == source_id
                ),
                None,
            )

        try:
            wire = base64.b64decode(encoded, validate=True)
            transferred = MessageEnvelope.from_proto(protocol_pb2.MessageEnvelope.FromString(wire))
        except (ValueError, KeyError, binascii.Error, DecodeError) as error:
            raise ValueError("invalid canonical source envelope") from error
        self._validate_source(transferred, source_id, details)

        existing = next(
            (
                item
                for item in await self.store.replay(self.engine.session_id)
                if item.message_id == source_id
            ),
            None,
        )
        if existing is not None:
            self._validate_source(existing, source_id, details)
            if existing.content_sha256 != transferred.content_sha256:
                raise ValueError("canonical source conflicts with persisted envelope")
            return existing
        return transferred

    @staticmethod
    def _tool_details(result: object) -> dict[object, object] | None:
        if not isinstance(result, dict):
            return None
        details = result.get("details")
        if not isinstance(details, dict):
            return None
        return details

    @staticmethod
    def _detail_text(details: dict[object, object], key: str) -> str | None:
        value = details.get(key)
        return str(value) if value else None

    def _validate_source(
        self,
        source: MessageEnvelope,
        source_id: str,
        details: dict[object, object],
    ) -> None:
        expected_hash = self._detail_text(details, "contentSha256")
        expected_locator = self._detail_text(details, "locator")
        if source.source is not SourceKind.TEXT_SOURCE:
            raise ValueError("tool result did not carry a canonical text source")
        if source.session_id != self.engine.session_id:
            raise ValueError("canonical source belongs to another session")
        if source.message_id != source_id:
            raise ValueError("canonical source message ID does not match tool result")
        if source.content.inline_text is None:
            raise ValueError("canonical text source must be inline")
        if expected_hash is None or source.content_sha256.hex() != expected_hash:
            raise ValueError("canonical source hash does not match tool result")
        if source.provenance.source_sha256 != source.content_sha256:
            raise ValueError("canonical source provenance hash does not match content")
        if expected_locator is not None and source.provenance.locator != expected_locator:
            raise ValueError("canonical source locator does not match tool result")
