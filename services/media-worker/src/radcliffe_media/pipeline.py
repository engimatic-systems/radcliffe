from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from radcliffe_domain.envelope import MessageEnvelope, SourceKind
from radcliffe_domain.projection import ContextPacket, context_projection, render_projection
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


class Orchestrator(Protocol):
    def turn(
        self, request: dict[str, object], cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]: ...


class Synthesizer(Protocol):
    async def synthesize(self, canonical_text: str) -> bytes: ...


class AudioPublisher(Protocol):
    async def publish(self, segment_id: str, pcm_audio: bytes) -> int: ...

    async def flush(self) -> None: ...


def _packet(packet: ContextPacket, timestamp: int) -> dict[str, object]:
    return {
        "projectionVersion": packet.projection_version,
        "messageId": packet.message_id,
        "role": packet.role,
        "content": packet.content,
        "source": packet.source.value,
        "annotations": [list(item) for item in packet.annotations],
        "include": packet.include,
        "timestamp": timestamp,
    }


class VoicePipeline:
    def __init__(
        self,
        session_id: str,
        store: EventStore,
        orchestrator: Orchestrator,
        synthesizer: Synthesizer,
        publisher: AudioPublisher,
    ) -> None:
        self.store = store
        self.engine = SessionEngine(session_id, store)
        self.orchestrator = orchestrator
        self.synthesizer = synthesizer
        self.publisher = publisher
        self._cancelled = asyncio.Event()

    async def start(self) -> None:
        self.engine.connect()
        self.engine.ready()

    async def barge_in(self) -> None:
        self._cancelled.set()
        await self.publisher.flush()
        self.engine.interrupt()

    async def handle_transcript(self, text: str) -> None:
        if self.engine.state is not ConversationState.LISTENING:
            await self.barge_in()
        self._cancelled = asyncio.Event()
        user = MessageEnvelope.text(
            session_id=self.engine.session_id,
            sequence=await self.store.next_sequence(self.engine.session_id),
            source=SourceKind.USER,
            text=text,
        )
        await self.store.append(user)
        operation = self.engine.begin(ConversationState.THINKING)
        history = await self.store.replay(self.engine.session_id)
        request_id = str(uuid4())
        request: dict[str, object] = {
            "requestId": request_id,
            "sessionId": self.engine.session_id,
            "generation": self.engine.generation,
            "packets": [
                _packet(context_projection(item), int(item.created_at.timestamp() * 1000))
                for item in history
            ],
        }
        buffer = ""
        full_model = ""
        source_rendered = False
        async for event in self.orchestrator.turn(request, self._cancelled):
            if self._cancelled.is_set():
                break
            if event.type == "model_delta":
                delta = str(event.payload.get("text", ""))
                full_model += delta
                if not source_rendered:
                    buffer += delta
                    buffer = await self._speak_complete_sentences(buffer, operation)
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
                            self.engine.transition(ConversationState.READING)
                        await self._speak_plan(source, operation)
                        source_rendered = True
                        buffer = ""
            elif event.type == "model_complete":
                full_model = str(event.payload.get("text", full_model))
            elif event.type == "error":
                self.engine.transition(ConversationState.FAILED)
                raise RuntimeError(str(event.payload.get("message", "sidecar failed")))

        if full_model:
            model = MessageEnvelope.text(
                session_id=self.engine.session_id,
                sequence=await self.store.next_sequence(self.engine.session_id),
                source=SourceKind.MODEL,
                text=full_model,
            )
            await self.store.append(model)
        if buffer and not source_rendered and not self._cancelled.is_set():
            if self.engine.state is ConversationState.THINKING:
                self.engine.transition(ConversationState.SPEAKING)
            await self._speak(buffer, f"{operation.operation_id}:final", 0, operation)
        if not self._cancelled.is_set() and self.engine.state is not ConversationState.FAILED:
            self.engine.transition(ConversationState.LISTENING)

    async def _speak_complete_sentences(self, buffer: str, operation: Operation) -> str:
        matches = list(_SENTENCE.finditer(buffer))
        if not matches:
            return buffer
        if self.engine.state is ConversationState.THINKING:
            self.engine.transition(ConversationState.SPEAKING)
        end = matches[-1].end()
        start = 0
        for index, match in enumerate(matches):
            await self._speak(
                buffer[start : match.end()],
                f"{operation.operation_id}:model:{index}",
                index,
                operation,
            )
            start = match.end()
        return buffer[end:]

    async def _speak_plan(self, envelope: MessageEnvelope, operation: Operation) -> None:
        plan = render_projection(envelope)
        for segment in plan.segments:
            await self._speak(
                segment.canonical_text,
                segment.segment_id,
                segment.boundary_index,
                operation,
            )

    async def _speak(
        self,
        canonical_text: str,
        segment_id: str,
        boundary_index: int,
        operation: Operation,
    ) -> None:
        self.engine.assert_current(operation)
        audio = await self.synthesizer.synthesize(canonical_text)
        if self._cancelled.is_set():
            return
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
        duration = await self.publisher.publish(segment_id, audio)
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

    @staticmethod
    def _source_message_id(result: object) -> str | None:
        if not isinstance(result, dict):
            return None
        details = result.get("details")
        if not isinstance(details, dict):
            return None
        value = details.get("sourceMessageId")
        return str(value) if value else None
