"""Resume a long source after a conversational detour.

Reproduces ses_853b2faaef57834a4319a00bd0adb0f6, where the user asked for the Gwern
nicotine article, interrupted at "Obvious enough..." to ask a question, and then said
"pick up where we left off". Every resume restarted at sentence zero, because playback
position was keyed by tool call ID and each resume issued a fresh read_source call.

The article is supplied as a fixture so these scenarios never touch the network or the
background job that originally fetched it.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from radcliffe_domain.envelope import SourceKind
from radcliffe_domain.projection import (
    PLAYBACK_BOUNDARY_INDEX_METADATA,
    PLAYBACK_COMPLETE_METADATA,
    SOURCE_CONTENT_SHA256_METADATA,
)
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import CapabilityGrant, ToolGateway
from radcliffe_media.pipeline import VoicePipeline
from radcliffe_media.sidecar import SidecarEvent

ARTICLE = Path(__file__).resolve().parents[3] / "fixtures" / "sources" / "gwern-nicotine.md"
# The sentence the user was hearing when they interrupted, in the real session.
INTERRUPTED_AT = 9
FIRST_SENTENCE = "[IQ](/doc/iq/index)"
RESUME_SENTENCE = "Less obvious is that nicotine"


class FakeSynthesizer:
    supports_speech_rate = True

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes:
        del rate
        return canonical_text.encode()


class PausingPublisher:
    """Publishes synchronously, but can block before one nominated source sentence.

    Blocking lets a test barge in at an exact, reproducible point in the article
    rather than racing the reader.
    """

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []
        self.block_at_boundary: int | None = None
        self.reached = asyncio.Event()
        self.flushed = False

    def duration_ms(self, pcm_audio: bytes) -> int:
        return len(pcm_audio)

    async def publish(
        self,
        segment_id: str,
        pcm_audio: bytes,
        *,
        wait_for_playback: bool = False,
    ) -> int:
        del wait_for_playback
        if segment_id.startswith("source:"):
            boundary = int(segment_id.rsplit(":", 1)[1])
            if boundary == self.block_at_boundary:
                self.block_at_boundary = None
                self.reached.set()
                await asyncio.Event().wait()  # released only by cancellation
        self.spoken.append((segment_id, pcm_audio.decode()))
        return len(pcm_audio)

    async def flush(self) -> None:
        self.flushed = True

    def source_boundaries(self) -> list[int]:
        return [
            int(segment_id.rsplit(":", 1)[1])
            for segment_id, _ in self.spoken
            if segment_id.startswith("source:")
        ]

    def source_text(self) -> str:
        return "".join(text for segment_id, text in self.spoken if segment_id.startswith("source:"))


class ScriptedOrchestrator:
    """Drives one scripted action per turn, issuing a fresh call ID for every read.

    A new call ID per read is the production behaviour that made resume impossible:
    it renames every playback segment, so a call-keyed lookup finds no prior progress.
    """

    def __init__(self, gateway: ToolGateway, grant: CapabilityGrant, path: str) -> None:
        self.gateway = gateway
        self.grant = grant
        self.path = path
        self.script: list[tuple[str, object]] = []
        self.call_ids: list[str] = []

    def will_read(self, start_at_boundary: int | None = None) -> None:
        self.script.append(("read", start_at_boundary))

    def will_answer(self, text: str) -> None:
        self.script.append(("answer", text))

    async def turn(
        self, request: dict, cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]:
        del cancelled
        action, payload = self.script.pop(0)
        if action == "answer":
            yield SidecarEvent("model_delta", {"text": str(payload)})
            yield SidecarEvent("model_complete", {"text": str(payload)})
            yield SidecarEvent("complete", {})
            return

        call_id = f"toolu_{len(self.call_ids):02d}"
        self.call_ids.append(call_id)
        generation = int(request["generation"])
        result = await self.gateway.read_source(
            call_id=call_id,
            idempotency_key=call_id,
            session_id=str(request["sessionId"]),
            generation=generation,
            # The control plane mints a grant for the requesting generation; a barge-in
            # advances the generation, so a pinned grant would go stale mid-conversation.
            grant=replace(self.grant, generation=generation),
            raw_arguments={
                "path": self.path,
                **({"start_at_boundary": payload} if payload is not None else {}),
            },
        )
        source = result.source_envelope
        details = {
            "sourceMessageId": source.message_id,
            "contentSha256": source.content_sha256.hex(),
            "locator": source.provenance.locator,
            "sourceEnvelopeProtoBase64": base64.b64encode(
                source.to_proto().SerializeToString(deterministic=True)
            ).decode("ascii"),
        }
        if result.start_at_boundary is not None:
            details["startAtBoundary"] = str(result.start_at_boundary)
        yield SidecarEvent("tool_end", {"isError": False, "result": {"details": details}})
        yield SidecarEvent("complete", {})


async def build_pipeline(
    tmp_path: Path,
) -> tuple[VoicePipeline, ScriptedOrchestrator, PausingPublisher]:
    root = tmp_path / "sources"
    root.mkdir()
    (root / "nicotine.md").write_bytes(ARTICLE.read_bytes())
    gateway_store = EventStore(tmp_path / "gateway.sqlite3")
    await gateway_store.migrate()
    worker_store = EventStore(tmp_path / "worker.sqlite3")
    await worker_store.migrate()
    grant = CapabilityGrant(
        grant_id="grant-1",
        session_id="s1",
        generation=1,
        readable_roots=(root,),
        allowed_tools=frozenset({"read_source"}),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    orchestrator = ScriptedOrchestrator(ToolGateway(gateway_store), grant, "nicotine.md")
    publisher = PausingPublisher()
    pipeline = VoicePipeline("s1", worker_store, orchestrator, FakeSynthesizer(), publisher)
    await pipeline.start()
    return pipeline, orchestrator, publisher


async def read_until_interrupted(
    pipeline: VoicePipeline,
    orchestrator: ScriptedOrchestrator,
    publisher: PausingPublisher,
    *,
    stop_before: int,
) -> None:
    """Read the article aloud and barge in just before a nominated sentence."""

    orchestrator.will_read()
    publisher.block_at_boundary = stop_before
    turn = asyncio.create_task(pipeline.handle_transcript("Read me the nicotine article"))
    await asyncio.wait_for(publisher.reached.wait(), timeout=5)
    await pipeline.barge_in()
    await asyncio.gather(turn, return_exceptions=True)


async def converse_and_interrupt(
    pipeline: VoicePipeline,
    orchestrator: ScriptedOrchestrator,
) -> None:
    """Ask a question about the article, then interrupt the answer to ask for a resume."""

    orchestrator.will_answer("Nicotine spray is a nasal delivery form of replacement therapy.")
    await pipeline.handle_transcript("What is the spray form of nicotine he's talking about?")
    await pipeline.barge_in()


@pytest.mark.asyncio
async def test_plain_resume_continues_after_a_conversational_detour(tmp_path) -> None:
    """rad-fksk: a bare read_source resumes at the first unheard sentence."""

    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)
    await read_until_interrupted(pipeline, orchestrator, publisher, stop_before=INTERRUPTED_AT + 1)

    heard = publisher.source_boundaries()
    assert heard == list(range(INTERRUPTED_AT + 1))
    assert "Obvious enough" in publisher.source_text()

    await converse_and_interrupt(pipeline, orchestrator)

    # "Okay, pick up where we left off." The model names no boundary at all.
    publisher.spoken.clear()
    orchestrator.will_read()
    await pipeline.handle_transcript("Okay, pick up where we left off.")

    resumed = publisher.source_boundaries()
    assert resumed[0] == INTERRUPTED_AT + 1, "resume restarted the article instead of continuing"
    assert resumed == sorted(resumed)
    assert publisher.source_text().startswith(RESUME_SENTENCE)
    assert FIRST_SENTENCE not in publisher.source_text()
    # Each resume is a distinct tool call, which is exactly what used to defeat resume.
    assert len(set(orchestrator.call_ids)) == len(orchestrator.call_ids)


@pytest.mark.asyncio
async def test_model_can_resume_at_an_explicit_boundary(tmp_path) -> None:
    """rad-bbep: the model names the boundary it read in its own context."""

    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)
    await read_until_interrupted(pipeline, orchestrator, publisher, stop_before=INTERRUPTED_AT + 1)
    await converse_and_interrupt(pipeline, orchestrator)

    publisher.spoken.clear()
    orchestrator.will_read(start_at_boundary=INTERRUPTED_AT + 1)
    await pipeline.handle_transcript("Pick up right where you stopped.")

    assert publisher.source_boundaries()[0] == INTERRUPTED_AT + 1
    assert publisher.source_text().startswith(RESUME_SENTENCE)


@pytest.mark.asyncio
async def test_model_can_back_up_before_the_heard_position(tmp_path) -> None:
    """An explicit boundary overrides the ledger, so "back up a bit" works."""

    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)
    await read_until_interrupted(pipeline, orchestrator, publisher, stop_before=INTERRUPTED_AT + 1)
    await converse_and_interrupt(pipeline, orchestrator)

    publisher.spoken.clear()
    orchestrator.will_read(start_at_boundary=INTERRUPTED_AT - 1)
    await pipeline.handle_transcript("Back up a couple of sentences and read from there.")

    assert publisher.source_boundaries()[0] == INTERRUPTED_AT - 1
    assert "Obvious enough" in publisher.source_text()


@pytest.mark.asyncio
async def test_model_can_skip_ahead_of_the_heard_position(tmp_path) -> None:
    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)
    await read_until_interrupted(pipeline, orchestrator, publisher, stop_before=INTERRUPTED_AT + 1)
    await converse_and_interrupt(pipeline, orchestrator)

    publisher.spoken.clear()
    orchestrator.will_read(start_at_boundary=INTERRUPTED_AT + 3)
    await pipeline.handle_transcript("Skip ahead a bit.")

    assert publisher.source_boundaries()[0] == INTERRUPTED_AT + 3
    assert RESUME_SENTENCE not in publisher.source_text()


@pytest.mark.asyncio
async def test_out_of_range_boundary_fails_instead_of_restarting(tmp_path) -> None:
    """Silently restarting from zero is the failure this work exists to remove."""

    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)
    await read_until_interrupted(pipeline, orchestrator, publisher, stop_before=INTERRUPTED_AT + 1)

    publisher.spoken.clear()
    orchestrator.will_read(start_at_boundary=9999)
    with pytest.raises(ValueError, match="beyond the end of the source"):
        await pipeline.handle_transcript("Resume the article.")

    assert publisher.source_boundaries() == []


@pytest.mark.asyncio
async def test_an_unheard_document_still_starts_at_the_beginning(tmp_path) -> None:
    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)

    orchestrator.will_read()
    await pipeline.handle_transcript("Read me the nicotine article")

    assert publisher.source_boundaries()[0] == 0
    assert publisher.source_text().startswith(FIRST_SENTENCE)


@pytest.mark.asyncio
async def test_heard_ledger_records_content_keyed_boundaries(tmp_path) -> None:
    """The durable evidence resume depends on: content hash plus boundary, per sentence."""

    pipeline, orchestrator, publisher = await build_pipeline(tmp_path)
    await read_until_interrupted(pipeline, orchestrator, publisher, stop_before=INTERRUPTED_AT + 1)

    heard = [
        item for item in await pipeline.store.replay("s1") if item.source is SourceKind.TEXT_SOURCE
    ]
    digests = {item.metadata[SOURCE_CONTENT_SHA256_METADATA] for item in heard}
    assert len(digests) == 1, "one document must produce one content key"
    assert [int(item.metadata[PLAYBACK_BOUNDARY_INDEX_METADATA]) for item in heard] == list(
        range(INTERRUPTED_AT + 1)
    )
    assert all(item.metadata[PLAYBACK_COMPLETE_METADATA] == "true" for item in heard)
