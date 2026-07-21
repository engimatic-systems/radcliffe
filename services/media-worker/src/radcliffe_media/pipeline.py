from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
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
    PROVIDER_BUDGET_EVENT,
    SESSION_TRANSITION_EVENT,
    TOOL_FAILURE_EVENT,
    TURN_DISPOSITION_EVENT,
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
    SpeechDelivery,
    context_replay,
    render_projection,
)
from radcliffe_domain.session import (
    ConversationState,
    Operation,
    PlaybackAcknowledgement,
    PlaybackStatus,
    SessionEngine,
    StaleOperationError,
)
from radcliffe_domain.store import EventStore
from radcliffe_domain.telemetry import TelemetrySink, session_pseudonym
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_media.sidecar import SidecarEvent

_SENTENCE = re.compile(r".+?[.!?](?:[\"')\]]*)\s+", re.DOTALL)
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
    model_persisted: bool = False
    next_model_boundary: int = 0
    started_at: float = field(default_factory=monotonic)
    first_model_delta_at: float | None = None
    first_audio_at: float | None = None
    source_envelope: MessageEnvelope | None = None
    next_source_boundary: int = 0
    model_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _PausedSource:
    envelope: MessageEnvelope
    turn_id: str
    operation: Operation


@dataclass(slots=True)
class _PendingPlayback:
    operation: Operation
    playback_id: str
    segment_id: str
    boundary_index: int
    duration_ms: int
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False
    latest_position_ms: int = 0
    terminal_status: PlaybackStatus | None = None


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
        defer_readiness_until_input: bool = False,
        configuration_registry: ConfigurationRegistry | None = None,
        configuration_owner_id: str = "local-owner",
        configuration_device_id: str | None = None,
    ) -> None:
        if model_chunk_chars <= 0:
            raise ValueError("model chunk size must be positive")
        if playback_ack_timeout_seconds <= 0:
            raise ValueError("playback acknowledgement timeout must be positive")
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
        self._paused_source: _PausedSource | None = None
        self._pending_playback: dict[str, _PendingPlayback] = {}
        self._playback_lock = asyncio.Lock()

    async def start(self) -> None:
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
    ) -> None:
        turn = self._active_turn
        operation = turn.operation if turn is not None else None
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
        await self._cancel_active("interrupted", always_flush=True)
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
            await self._notify_state(previous, "interrupted")

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
        self._paused_source = None
        previous = self.engine.state
        self.engine.end()
        if previous is not ConversationState.IDLE:
            await self._notify_state(previous, "session ended")

    async def handle_transcript(self, text: str) -> None:
        if self._closed_for_handoff:
            return
        submission = await self._begin_submission()
        turn_id = str(uuid4())
        async with self._turn_serial:
            await self._append_text(
                SourceKind.USER,
                text,
                {TURN_ID_METADATA: turn_id},
                notify=True,
            )
            task = cast(asyncio.Task[None], asyncio.current_task())
            turn = _ActiveTurn(turn_id, task, asyncio.Event())
            async with self._coordinator:
                if submission != self._latest_submission:
                    stale = True
                else:
                    stale = False
                    self._active_turn = turn
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
                )
                await self._persist_model(turn, notify=True)
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
            "responseDetail": await self._effective_configuration(
                ConfigurationKey.RESPONSE_DETAIL,
                turn.turn_id,
            ),
            "packets": [
                packet.to_wire(int(item.created_at.timestamp() * 1000))
                for item, packet in zip(history, packets, strict=True)
            ],
        }
        buffer = ""
        source_rendered = False
        async for event in self.orchestrator.turn(request, turn.cancelled):
            if turn.cancelled.is_set():
                break
            self._assert_owner(turn, operation)
            if event.type == "model_delta" and not source_rendered:
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
                            if isinstance(category, str) and category in _SPOKEN_OUTPUT_CATEGORIES
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
                        turn.model_metadata.get("radcliffe.spoken_output.categories", "").split(",")
                    )
                    existing.discard("")
                    turn.model_metadata["radcliffe.spoken_output.categories"] = ",".join(
                        sorted(existing | set(categories))
                    )
                if not source_rendered:
                    buffer += spoken_delta
                    buffer = await self._speak_complete_sentences(buffer, operation, turn)
            elif event.type == "tool_end" and bool(event.payload.get("isError")):
                raw_tool_name = event.payload.get("toolName")
                tool_name = (
                    raw_tool_name
                    if isinstance(raw_tool_name, str) and raw_tool_name in _REGISTERED_TOOL_NAMES
                    else "unknown"
                )
                result = event.payload.get("result")
                result_details = result.get("details") if isinstance(result, dict) else None
                recoverable = (
                    isinstance(result_details, dict)
                    and result_details.get("recoverable") is True
                    and result_details.get("errorCode") == "invalid_arguments"
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
                await self._append_text(
                    SourceKind.SYSTEM,
                    "Tool arguments rejected" if recoverable else "Tool call failed",
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
                    if self.engine.state is ConversationState.THINKING:
                        await self._transition(
                            ConversationState.READING,
                            "source authorized",
                            operation,
                            turn,
                        )
                    if self.events is not None:
                        await self.events.message(source)
                    await self._speak_plan(source, operation, turn)
                    source_rendered = True
                    buffer = ""
                    turn.model_text = turn.rendered_model_text
                else:
                    result = event.payload.get("result")
                    if event.payload.get("toolName") == "run_job":
                        await self._persist_background_job_reference(result, turn)
                    if await self._handle_session_action_tool_result(result, turn):
                        await self._finish_handoff_from_turn(turn)
                        return
                    await self._persist_configuration_update(result, turn)
            elif event.type == "model_complete" and not source_rendered:
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

        self._assert_owner(turn, operation)
        if buffer and not source_rendered:
            await self._speak_model_chunk(buffer, operation, turn, "model response ready")
        if turn.model_text:
            await self._persist_model(turn, notify=True)
        await self._finish_completed(turn)

    async def _speak_complete_sentences(
        self, buffer: str, operation: Operation, turn: _ActiveTurn
    ) -> str:
        while chunk := self._next_model_chunk(buffer):
            await self._speak_model_chunk(chunk, operation, turn, "model chunk ready")
            buffer = buffer[len(chunk) :]
        return buffer

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
    ) -> None:
        if self.engine.state is ConversationState.THINKING:
            await self._transition(ConversationState.SPEAKING, reason, operation, turn)
        boundary = turn.next_model_boundary
        await self._speak(
            canonical_text,
            f"{operation.operation_id}:model:{boundary}",
            boundary,
            operation,
            turn,
            source=SourceKind.MODEL,
        )
        turn.rendered_model_text += canonical_text
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
        if self.events is not None:
            await self.events.state_changed(
                previous,
                self.engine.state,
                self.engine.generation,
                reason,
                operation,
            )

    async def _speak_plan(
        self, envelope: MessageEnvelope, operation: Operation, turn: _ActiveTurn
    ) -> None:
        plan = render_projection(envelope)
        turn.source_envelope = envelope
        for segment in plan.segments:
            if segment.boundary_index < turn.next_source_boundary:
                continue
            await self._speak(
                segment.canonical_text,
                segment.segment_id,
                segment.boundary_index,
                operation,
                turn,
                source=SourceKind.TEXT_SOURCE,
                delivery=segment.delivery if segment.delivery_overridden else None,
                wait_for_playback=True,
            )
            turn.next_source_boundary = segment.boundary_index + 1

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
        plan = render_projection(envelope)
        boundaries = [
            await self.store.playback_boundary(self.engine.session_id, segment.segment_id)
            for segment in plan.segments
        ]
        return max(boundaries, default=0)

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
    ) -> None:
        self._assert_owner(turn, operation)
        delivery = delivery or SpeechDelivery.parse(
            await self._effective_configuration(
                ConfigurationKey.DEFAULT_SPEECH_RATE,
                turn.turn_id,
            )
        )
        synth_started = monotonic()
        if getattr(self.synthesizer, "supports_speech_rate", False):
            audio = await self.synthesizer.synthesize(
                canonical_text,
                rate=delivery.multiplier,
            )
        elif delivery.is_default:
            audio = await self.synthesizer.synthesize(canonical_text)
        else:
            raise RuntimeError("speech provider does not support a non-default delivery rate")
        duration = self.publisher.duration_ms(audio)
        playback_id = str(uuid4())
        self._log_phase(turn, "synthesis", monotonic() - synth_started)
        self._assert_owner(turn, operation)
        pending: _PendingPlayback | None = None
        if self.client_playback_acknowledgements:
            if self.events is None:
                raise RuntimeError("client playback authority requires an event sink")
            pending = _PendingPlayback(
                operation,
                playback_id,
                segment_id,
                boundary_index,
                duration,
            )
            async with self._playback_lock:
                self._pending_playback[playback_id] = pending
            await self.events.playback_segment(
                operation,
                playback_id,
                segment_id,
                boundary_index,
                duration,
                hashlib.sha256(canonical_text.encode()).digest(),
                source,
                delivery.multiplier,
            )
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
                wait_for_playback=wait_for_playback,
            )
        except asyncio.CancelledError:
            if not self.client_playback_acknowledgements:
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
            if not self.client_playback_acknowledgements:
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
            if wait_for_playback and pending is not None:
                try:
                    await asyncio.wait_for(
                        pending.terminal.wait(),
                        timeout=self.playback_ack_timeout_seconds,
                    )
                except TimeoutError as error:
                    raise RuntimeError(
                        f"client did not acknowledge playback segment {segment_id}"
                    ) from error
                if pending.terminal_status is not PlaybackStatus.COMPLETED:
                    raise asyncio.CancelledError
        else:
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

    async def playback_acknowledgement(self, acknowledgement: PlaybackAcknowledgement) -> bool:
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
            elif acknowledgement.status is PlaybackStatus.STARTED:
                if pending.started or acknowledgement.position_ms != 0:
                    raise ValueError("playback started acknowledgement is invalid")
                pending.started = True
            elif acknowledgement.status is PlaybackStatus.POSITION:
                if not pending.started:
                    raise ValueError("playback position arrived before playback started")
            elif acknowledgement.status is PlaybackStatus.COMPLETED:
                if not pending.started:
                    raise ValueError("playback completion arrived before playback started")
                if acknowledgement.position_ms < pending.duration_ms:
                    raise ValueError("playback completed before the announced duration")
                pending.terminal_status = acknowledgement.status
            elif acknowledgement.status in {PlaybackStatus.FLUSHED, PlaybackStatus.FAILED}:
                pending.terminal_status = acknowledgement.status
            else:
                raise ValueError("unsupported playback acknowledgement status")
            pending.latest_position_ms = acknowledgement.position_ms
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
            if pending.terminal_status is not None:
                pending.terminal.set()
            return persisted

    async def _begin_submission(self) -> int:
        async with self._coordinator:
            self._latest_submission += 1
            submission = self._latest_submission
        await self._cancel_active("superseded")
        return submission

    async def _cancel_active(self, disposition: str, *, always_flush: bool = False) -> None:
        task: asyncio.Task[None] | None = None
        should_flush = always_flush
        async with self._coordinator:
            turn = self._active_turn
            if turn is not None:
                should_flush = True
                turn.requested_disposition = disposition
                if not turn.cancelled.is_set():
                    turn.cancelled.set()
                    task = turn.task
                    task.cancel()
        if should_flush:
            await self.publisher.flush()
        current = asyncio.current_task()
        if task is not None and task is not current:
            await asyncio.gather(task, return_exceptions=True)
            await self.publisher.flush()

    async def _finish_completed(self, turn: _ActiveTurn) -> None:
        if turn.operation is not None:
            await self._await_operation_playback(turn.operation)
        async with self._coordinator:
            self._assert_owner(turn, turn.operation)
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
                if item.operation == operation and item.terminal_status is None
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
            if turn.rendered_model_text and not turn.model_persisted:
                await self._persist_model(
                    turn,
                    partial=True,
                    notify=True,
                    text=turn.rendered_model_text,
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
            if turn.rendered_model_text and not turn.model_persisted:
                await self._persist_model(
                    turn,
                    partial=True,
                    notify=True,
                    text=turn.rendered_model_text,
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

        async with self._append_lock:
            proto = transferred.to_proto()
            proto.sequence = await self.store.next_sequence(self.engine.session_id)
            imported = MessageEnvelope.from_proto(proto)
            await self.store.append(imported)
        return imported

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
