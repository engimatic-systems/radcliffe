from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol, cast
from uuid import uuid4

from loguru import logger
from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.projection import (
    TURN_DISPOSITION_EVENT,
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
    context_replay,
    render_projection,
)
from radcliffe_domain.session import (
    ConversationState,
    Operation,
    PlaybackAcknowledgement,
    PlaybackStatus,
    SessionEngine,
)
from radcliffe_domain.store import EventStore

from radcliffe_media.sidecar import SidecarEvent

_SENTENCE = re.compile(r".+?[.!?](?:[\"')\]]*)\s+", re.DOTALL)


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


class Orchestrator(Protocol):
    def turn(
        self, request: dict[str, object], cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]: ...


class Synthesizer(Protocol):
    async def synthesize(self, canonical_text: str) -> bytes: ...


class AudioPublisher(Protocol):
    async def publish(self, segment_id: str, pcm_audio: bytes) -> int: ...

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


class VoicePipeline:
    def __init__(
        self,
        session_id: str,
        store: EventStore,
        orchestrator: Orchestrator,
        synthesizer: Synthesizer,
        publisher: AudioPublisher,
        events: VoiceEventSink | None = None,
        *,
        model_chunk_chars: int = 240,
    ) -> None:
        if model_chunk_chars <= 0:
            raise ValueError("model chunk size must be positive")
        self.store = store
        self.engine = SessionEngine(session_id, store)
        self.orchestrator = orchestrator
        self.synthesizer = synthesizer
        self.publisher = publisher
        self.events = events
        self.model_chunk_chars = model_chunk_chars
        self._active_turn: _ActiveTurn | None = None
        self._coordinator = asyncio.Lock()
        self._turn_serial = asyncio.Lock()
        self._append_lock = asyncio.Lock()
        self._latest_submission = 0

    async def start(self) -> None:
        previous = self.engine.state
        self.engine.connect()
        await self._notify_state(previous, "session connected")
        previous = self.engine.state
        self.engine.ready()
        await self._notify_state(previous, "session ready")

    async def barge_in(self) -> None:
        await self._cancel_active("interrupted", always_flush=True)

    async def pause(self) -> None:
        if self.engine.state is not ConversationState.LISTENING:
            await self.barge_in()
        if self.engine.state is ConversationState.LISTENING:
            previous = self.engine.state
            self.engine.pause()
            await self._notify_state(previous, "paused")

    async def resume(self) -> None:
        if self.engine.state is ConversationState.PAUSED:
            previous = self.engine.state
            self.engine.resume()
            await self._notify_state(previous, "resumed")

    async def stop(self) -> None:
        await self._cancel_active("interrupted", always_flush=True)
        previous = self.engine.state
        self.engine.end()
        if previous is not ConversationState.IDLE:
            await self._notify_state(previous, "session ended")

    async def handle_transcript(self, text: str) -> None:
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

    async def _run_turn(self, turn: _ActiveTurn) -> None:
        self._assert_owner(turn)
        previous = self.engine.state
        operation = self.engine.begin(ConversationState.THINKING)
        turn.operation = operation
        await self._notify_state(previous, "transcription complete", operation)
        history = await self.store.replay(self.engine.session_id)
        packets = context_replay(history)
        request_id = str(uuid4())
        request: dict[str, object] = {
            "requestId": request_id,
            "sessionId": self.engine.session_id,
            "generation": self.engine.generation,
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
            if event.type == "model_delta":
                delta = str(event.payload.get("text", ""))
                if turn.first_model_delta_at is None:
                    turn.first_model_delta_at = monotonic()
                    self._log_phase(
                        turn,
                        "first_model_delta",
                        turn.first_model_delta_at - turn.started_at,
                    )
                turn.model_text += delta
                if not source_rendered:
                    buffer += delta
                    buffer = await self._speak_complete_sentences(buffer, operation, turn)
            elif event.type == "tool_end" and not bool(event.payload.get("isError")):
                source_id = self._source_message_id(event.payload.get("result"))
                if source_id:
                    source = next(
                        (
                            item
                            for item in await self.store.replay(self.engine.session_id)
                            if item.message_id == source_id
                        ),
                        None,
                    )
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
            elif event.type == "model_complete":
                turn.model_text = str(event.payload.get("text", turn.model_text))
            elif event.type == "error":
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
        for segment in plan.segments:
            await self._speak(
                segment.canonical_text,
                segment.segment_id,
                segment.boundary_index,
                operation,
                turn,
            )

    async def _speak(
        self,
        canonical_text: str,
        segment_id: str,
        boundary_index: int,
        operation: Operation,
        turn: _ActiveTurn,
    ) -> None:
        self._assert_owner(turn, operation)
        synth_started = monotonic()
        audio = await self.synthesizer.synthesize(canonical_text)
        self._log_phase(turn, "synthesis", monotonic() - synth_started)
        self._assert_owner(turn, operation)
        now = datetime.now(UTC)
        await self.engine.acknowledge(
            PlaybackAcknowledgement(
                acknowledgement_id=str(uuid4()),
                operation=operation,
                segment_id=segment_id,
                status=PlaybackStatus.STARTED,
                position_ms=0,
                boundary_index=boundary_index,
                occurred_at=now,
            )
        )
        self._assert_owner(turn, operation)
        publish_started = monotonic()
        try:
            duration = await self.publisher.publish(segment_id, audio)
        except asyncio.CancelledError:
            raise
        except Exception as error:
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
                )
            )
            raise
        self._log_phase(turn, "publish", monotonic() - publish_started)
        if turn.first_audio_at is None:
            turn.first_audio_at = monotonic()
            self._log_phase(turn, "first_audio", turn.first_audio_at - turn.started_at)
        self._assert_owner(turn, operation)
        await self.engine.acknowledge(
            PlaybackAcknowledgement(
                acknowledgement_id=str(uuid4()),
                operation=operation,
                segment_id=segment_id,
                status=PlaybackStatus.COMPLETED,
                position_ms=duration,
                boundary_index=boundary_index + 1,
                occurred_at=datetime.now(UTC),
            )
        )

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
        async with self._coordinator:
            self._assert_owner(turn, turn.operation)
            previous = self.engine.state
            self.engine.transition(ConversationState.LISTENING)
            await self._append_disposition(turn, "completed")
            self._active_turn = None
            await self._notify_state(previous, "turn complete", turn.operation)

    async def _finish_cancelled(self, turn: _ActiveTurn) -> None:
        async with self._coordinator:
            if self._active_turn is not turn:
                return
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

    async def _finish_failed(self, turn: _ActiveTurn) -> None:
        async with self._coordinator:
            if self._active_turn is not turn:
                return
            try:
                await self.publisher.flush()
            except Exception:
                logger.exception("failed to flush audio after a voice turn error")
            if turn.rendered_model_text and not turn.model_persisted:
                await self._persist_model(
                    turn,
                    partial=True,
                    notify=True,
                    text=turn.rendered_model_text,
                )
            await self._append_disposition(turn, "failed")
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

    def _log_phase(self, turn: _ActiveTurn, phase: str, seconds: float) -> None:
        operation = turn.operation
        logger.info(
            "voice turn phase complete phase={} duration_ms={} session_id={} "
            "operation_id={} generation={}",
            phase,
            round(seconds * 1000, 2),
            self.engine.session_id,
            operation.operation_id if operation is not None else "pending",
            operation.generation if operation is not None else self.engine.generation,
        )

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

    @staticmethod
    def _source_message_id(result: object) -> str | None:
        if not isinstance(result, dict):
            return None
        details = result.get("details")
        if not isinstance(details, dict):
            return None
        value = details.get("sourceMessageId")
        return str(value) if value else None
