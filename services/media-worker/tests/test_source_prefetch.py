"""Synthesis runs ahead of playback, without running ahead of the record of what was heard.

rad-86le. Reading paused audibly between every sentence because _speak synthesized and then
waited for that sentence to finish playing before synthesizing the next one, so the gap was
the next sentence's synthesis latency in full.

The look-ahead prepares audio only. Registration, acknowledgement, and the heard ledger stay
where they were, in play order: audio is a pure function of text and rate and is safe to run
ahead, while "the user heard this" is a fact about the past and is not.
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
    SOURCE_CONTENT_SHA256_METADATA,
    heard_boundary,
)
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import CapabilityGrant, ToolGateway
from radcliffe_media.pipeline import VoicePipeline
from radcliffe_media.sidecar import SidecarEvent

ARTICLE = Path(__file__).resolve().parents[3] / "fixtures" / "sources" / "gwern-nicotine.md"


class RecordingSynthesizer:
    """Records every synthesis, and can fail on demand to prove failures do not hang."""

    supports_speech_rate = True

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.inputs: list[str] = []
        self.fail_on_call = fail_on_call

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes:
        del rate
        self.inputs.append(canonical_text)
        if self.fail_on_call is not None and len(self.inputs) == self.fail_on_call:
            raise RuntimeError("synthesizer exploded")
        return canonical_text.encode()


class BlockingPublisher:
    """Blocks inside one nominated sentence so a test can inspect the look-ahead mid-read."""

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []
        self.block_at_boundary: int | None = None
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

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
                self.reached.set()
                await self.release.wait()
        self.spoken.append((segment_id, pcm_audio.decode()))
        return len(pcm_audio)

    async def flush(self) -> None:
        return None

    def source_boundaries(self) -> list[int]:
        return [
            int(segment_id.rsplit(":", 1)[1])
            for segment_id, _ in self.spoken
            if segment_id.startswith("source:")
        ]


class ScaledDurationPublisher(BlockingPublisher):
    """Makes the real fixture's long prose exceed the receiver's 30-second cap."""

    milliseconds_per_byte = 100

    def duration_ms(self, pcm_audio: bytes) -> int:
        return len(pcm_audio) * self.milliseconds_per_byte

    async def publish(
        self,
        segment_id: str,
        pcm_audio: bytes,
        *,
        wait_for_playback: bool = False,
    ) -> int:
        await super().publish(
            segment_id,
            pcm_audio,
            wait_for_playback=wait_for_playback,
        )
        return self.duration_ms(pcm_audio)


class ReadingOrchestrator:
    def __init__(self, gateway: ToolGateway, grant: CapabilityGrant, path: str) -> None:
        self.gateway = gateway
        self.grant = grant
        self.path = path
        self.calls = 0

    async def turn(
        self, request: dict, cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]:
        del cancelled
        call_id = f"toolu_{self.calls:02d}"
        self.calls += 1
        generation = int(request["generation"])
        result = await self.gateway.read_source(
            call_id=call_id,
            idempotency_key=call_id,
            session_id=str(request["sessionId"]),
            generation=generation,
            grant=replace(self.grant, generation=generation),
            raw_arguments={"path": self.path},
        )
        source = result.source_envelope
        yield SidecarEvent(
            "tool_end",
            {
                "isError": False,
                "result": {
                    "details": {
                        "sourceMessageId": source.message_id,
                        "contentSha256": source.content_sha256.hex(),
                        "locator": source.provenance.locator,
                        "sourceEnvelopeProtoBase64": base64.b64encode(
                            source.to_proto().SerializeToString(deterministic=True)
                        ).decode("ascii"),
                    }
                },
            },
        )
        yield SidecarEvent("complete", {})


async def build_pipeline(
    tmp_path: Path,
    *,
    synthesizer: RecordingSynthesizer | None = None,
    publisher: BlockingPublisher | None = None,
) -> tuple[VoicePipeline, BlockingPublisher, RecordingSynthesizer, EventStore]:
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
    orchestrator = ReadingOrchestrator(ToolGateway(gateway_store), grant, "nicotine.md")
    publisher = publisher or BlockingPublisher()
    tts = synthesizer or RecordingSynthesizer()
    pipeline = VoicePipeline("s1", worker_store, orchestrator, tts, publisher)
    await pipeline.start()
    return pipeline, publisher, tts, worker_store


async def heard_boundaries(store: EventStore) -> list[int]:
    """Boundaries the ledger claims were actually heard, for the one source in the session."""

    return [
        boundary
        for envelope in await store.replay("s1")
        if envelope.source is SourceKind.TEXT_SOURCE
        and SOURCE_CONTENT_SHA256_METADATA in envelope.metadata
        and (boundary := heard_boundary(envelope.metadata)) is not None
    ]


@pytest.mark.asyncio
async def test_synthesis_runs_ahead_of_playback(tmp_path) -> None:
    """The point of the change: the next sentence is already rendered when this one ends."""

    pipeline, publisher, tts, _ = await build_pipeline(tmp_path)
    publisher.block_at_boundary = 0

    turn = asyncio.create_task(pipeline.handle_transcript("Read me the nicotine article"))
    await asyncio.wait_for(publisher.reached.wait(), timeout=5)

    # Playback of sentence 0 has not finished, yet later sentences are already audio.
    assert len(tts.inputs) > 1, "synthesis still waits for playback"
    # ...and the look-ahead is bounded rather than racing to the end of the article.
    assert len(tts.inputs) <= pipeline.source_prefetch_depth + 2

    publisher.release.set()
    await asyncio.wait_for(turn, timeout=10)


@pytest.mark.asyncio
async def test_long_source_boundaries_are_split_below_receiver_duration_capacity(
    tmp_path,
) -> None:
    publisher = ScaledDurationPublisher()
    pipeline, _, tts, _ = await build_pipeline(tmp_path, publisher=publisher)

    await pipeline.handle_transcript("Read me the nicotine article")

    source_audio = [
        (segment_id, text)
        for segment_id, text in publisher.spoken
        if segment_id.startswith("source:")
    ]
    assert source_audio
    assert any(":part:" in segment_id for segment_id, _text in source_audio)
    assert all(publisher.duration_ms(text.encode()) <= 30_000 for _segment_id, text in source_audio)
    accepted_synthesis = [
        text for text in tts.inputs if publisher.duration_ms(text.encode()) <= 30_000
    ]
    assert [text for _segment_id, text in source_audio] == accepted_synthesis


@pytest.mark.asyncio
async def test_source_subsegment_does_not_advance_the_logical_resume_boundary(
    tmp_path,
) -> None:
    publisher = ScaledDurationPublisher()
    publisher.block_at_boundary = 1
    pipeline, _, _, store = await build_pipeline(tmp_path, publisher=publisher)

    turn = asyncio.create_task(pipeline.handle_transcript("Read me the nicotine article"))
    await asyncio.wait_for(publisher.reached.wait(), timeout=5)

    assert await heard_boundaries(store) == [0]

    await pipeline.barge_in()
    await asyncio.wait_for(turn, timeout=5)


@pytest.mark.asyncio
async def test_prefetched_sentences_are_not_recorded_as_heard(tmp_path) -> None:
    """The invariant that protects resume: only played sentences enter the ledger."""

    pipeline, publisher, tts, store = await build_pipeline(tmp_path)
    publisher.block_at_boundary = 0

    turn = asyncio.create_task(pipeline.handle_transcript("Read me the nicotine article"))
    await asyncio.wait_for(publisher.reached.wait(), timeout=5)

    assert len(tts.inputs) > 1, "nothing was prefetched, so this proves nothing"
    assert await heard_boundaries(store) == [], "audio prepared ahead was recorded as heard"

    publisher.release.set()
    await asyncio.wait_for(turn, timeout=10)


@pytest.mark.asyncio
async def test_a_barge_in_discards_audio_prepared_ahead(tmp_path) -> None:
    """Nothing rendered ahead may be spoken after the user interrupts."""

    pipeline, publisher, tts, store = await build_pipeline(tmp_path)
    publisher.block_at_boundary = 2

    turn = asyncio.create_task(pipeline.handle_transcript("Read me the nicotine article"))
    await asyncio.wait_for(publisher.reached.wait(), timeout=5)
    prepared = len(tts.inputs)
    await pipeline.barge_in()
    publisher.release.set()
    await asyncio.gather(turn, return_exceptions=True)

    spoken = publisher.source_boundaries()
    assert spoken == [0, 1], "a sentence prepared ahead was spoken after the barge-in"
    assert prepared > len(spoken), "nothing was discarded, so this proves nothing"
    # The ledger records what was played, never what was merely prepared.
    assert max(await heard_boundaries(store), default=0) <= len(spoken)


@pytest.mark.asyncio
async def test_a_synthesis_failure_surfaces_instead_of_hanging_the_reader(tmp_path) -> None:
    """A dead prefetch task must not leave the reader waiting on a queue nobody will fill."""

    tts = RecordingSynthesizer(fail_on_call=2)
    pipeline, _, _, _ = await build_pipeline(tmp_path, synthesizer=tts)

    # Fails rather than blocking forever; wait_for would raise TimeoutError on a deadlock.
    with pytest.raises(RuntimeError, match="synthesizer exploded"):
        await asyncio.wait_for(
            pipeline.handle_transcript("Read me the nicotine article"),
            timeout=10,
        )
