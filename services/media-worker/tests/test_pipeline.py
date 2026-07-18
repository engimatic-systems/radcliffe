import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_domain.envelope import SourceKind
from radcliffe_domain.projection import TURN_DISPOSITION_METADATA, context_replay
from radcliffe_domain.session import ConversationState
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import CapabilityGrant, ToolGateway
from radcliffe_media.pipeline import VoicePipeline
from radcliffe_media.sidecar import SidecarEvent


class FakeSynthesizer:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def synthesize(self, canonical_text: str) -> bytes:
        self.inputs.append(canonical_text)
        return canonical_text.encode()


class FakePublisher:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.segment_ids: list[str] = []
        self.flushed = False
        self.published = asyncio.Event()

    async def publish(self, segment_id: str, pcm_audio: bytes) -> int:
        self.segment_ids.append(segment_id)
        self.audio.append(pcm_audio)
        self.published.set()
        return len(pcm_audio)

    async def flush(self) -> None:
        self.flushed = True


class FailFirstPublisher(FakePublisher):
    async def publish(self, segment_id: str, pcm_audio: bytes) -> int:
        if not self.segment_ids:
            self.segment_ids.append(segment_id)
            raise RuntimeError("deterministic publish failure")
        return await super().publish(segment_id, pcm_audio)


class FakeEvents:
    def __init__(self) -> None:
        self.messages = []
        self.states = []

    async def message(self, envelope) -> None:
        self.messages.append(envelope)

    async def state_changed(self, previous, current, generation, reason, operation=None) -> None:
        self.states.append((previous, current, generation, reason, operation))


class AnswerOrchestrator:
    async def turn(self, request, cancelled=None) -> AsyncIterator[SidecarEvent]:
        del request, cancelled
        yield SidecarEvent("model_delta", {"text": "First sentence. "})
        yield SidecarEvent("model_delta", {"text": "Second sentence."})
        yield SidecarEvent("model_complete", {"text": "First sentence. Second sentence."})
        yield SidecarEvent("complete", {})


class SourceOrchestrator:
    def __init__(self, gateway: ToolGateway, grant: CapabilityGrant) -> None:
        self.gateway = gateway
        self.grant = grant

    async def turn(self, request, cancelled=None) -> AsyncIterator[SidecarEvent]:
        del cancelled
        result = await self.gateway.read_source(
            call_id="call-1",
            idempotency_key="idem-1",
            session_id=str(request["sessionId"]),
            generation=int(request["generation"]),
            grant=self.grant,
            raw_arguments={"path": "demo.md"},
        )
        yield SidecarEvent(
            "tool_end",
            {
                "isError": False,
                "result": {"details": {"sourceMessageId": result.source_envelope.message_id}},
            },
        )
        yield SidecarEvent("complete", {})


class InterruptibleOrchestrator:
    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.requests: list[dict[str, object]] = []

    async def turn(self, request, cancelled=None) -> AsyncIterator[SidecarEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield SidecarEvent("model_delta", {"text": "One. "})
            self.first_started.set()
            await asyncio.Event().wait()
        yield SidecarEvent("model_delta", {"text": "I am the local model."})
        yield SidecarEvent("model_complete", {"text": "I am the local model."})
        yield SidecarEvent("complete", {})


class RepeatingInterruptOrchestrator:
    def __init__(self) -> None:
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.requests: list[dict[str, object]] = []

    async def turn(self, request, cancelled=None) -> AsyncIterator[SidecarEvent]:
        self.requests.append(request)
        packets = request["packets"]
        current = [packet["content"] for packet in packets if packet["include"]][-1]
        if str(current).startswith("abandon-"):
            yield SidecarEvent("model_delta", {"text": f"partial-{current}. "})
            await self.started.put(str(current))
            await asyncio.Event().wait()
        answer = f"answer-{current}"
        yield SidecarEvent("model_delta", {"text": answer})
        yield SidecarEvent("model_complete", {"text": answer})
        yield SidecarEvent("complete", {})


class RecoveringOrchestrator:
    def __init__(self) -> None:
        self.calls = 0

    async def turn(self, request, cancelled=None) -> AsyncIterator[SidecarEvent]:
        del request, cancelled
        self.calls += 1
        if self.calls == 1:
            yield SidecarEvent("model_delta", {"text": "Audible first. "})
            yield SidecarEvent("model_delta", {"text": "- **Cosmic touch:** Moon 🌙"})
            yield SidecarEvent(
                "model_complete",
                {"text": "Audible first. - **Cosmic touch:** Moon 🌙"},
            )
        else:
            yield SidecarEvent("model_delta", {"text": "Recovered."})
            yield SidecarEvent("model_complete", {"text": "Recovered."})
        yield SidecarEvent("complete", {})


class FailSecondSynthesis:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, canonical_text: str) -> bytes:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("deterministic synthesis failure")
        return canonical_text.encode()


class PunctuationPoorOrchestrator:
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.text = "- **Cosmic touch:** " + "🌑 🌒 🌓 🌔 🌕 " * 8 + "\n"

    async def turn(self, request, cancelled=None) -> AsyncIterator[SidecarEvent]:
        del request, cancelled
        yield SidecarEvent("model_delta", {"text": self.text})
        self.waiting.set()
        await self.release.wait()
        yield SidecarEvent("model_delta", {"text": "Done."})
        yield SidecarEvent("model_complete", {"text": self.text + "Done."})
        yield SidecarEvent("complete", {})


@pytest.mark.asyncio
async def test_interrupt_is_safe_while_listening(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    publisher = FakePublisher()
    pipeline = VoicePipeline("s1", store, AnswerOrchestrator(), FakeSynthesizer(), publisher)
    await pipeline.start()

    await pipeline.barge_in()

    assert pipeline.engine.state is ConversationState.LISTENING
    assert publisher.flushed


@pytest.mark.asyncio
async def test_model_sentences_stream_to_tts_and_are_persisted(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    tts = FakeSynthesizer()
    publisher = FakePublisher()
    events = FakeEvents()
    pipeline = VoicePipeline("s1", store, AnswerOrchestrator(), tts, publisher, events)
    await pipeline.start()

    await pipeline.handle_transcript("Tell me something")

    assert "".join(tts.inputs) == "First sentence. Second sentence."
    assert b"".join(publisher.audio) == b"First sentence. Second sentence."
    assert [item.source for item in await store.replay("s1")] == [
        SourceKind.USER,
        SourceKind.MODEL,
        SourceKind.SYSTEM,
    ]
    assert [item.source for item in events.messages] == [SourceKind.USER, SourceKind.MODEL]
    assert [state[1] for state in events.states] == [
        ConversationState.CONNECTING,
        ConversationState.LISTENING,
        ConversationState.THINKING,
        ConversationState.SPEAKING,
        ConversationState.LISTENING,
    ]


@pytest.mark.asyncio
async def test_source_bytes_bypass_model_and_reach_tts_exactly(tmp_path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    canonical = b"# Canonical\n\nFirst sentence.  Second sentence!\n"
    (root / "demo.md").write_bytes(canonical)
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    grant = CapabilityGrant(
        grant_id="grant-1",
        session_id="s1",
        generation=1,
        readable_roots=(root,),
        allowed_tools=frozenset({"read_source"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    tts = FakeSynthesizer()
    publisher = FakePublisher()
    pipeline = VoicePipeline(
        "s1", store, SourceOrchestrator(ToolGateway(store), grant), tts, publisher
    )
    await pipeline.start()

    await pipeline.handle_transcript("Read demo.md")

    assert "".join(tts.inputs).encode() == canonical
    assert b"".join(publisher.audio) == canonical
    assert SourceKind.TEXT_SOURCE in [item.source for item in await store.replay("s1")]


@pytest.mark.asyncio
async def test_superseded_turn_is_audited_but_cannot_bleed_into_next_request(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    orchestrator = InterruptibleOrchestrator()
    publisher = FakePublisher()
    events = FakeEvents()
    pipeline = VoicePipeline(
        "s1",
        store,
        orchestrator,
        FakeSynthesizer(),
        publisher,
        events,
    )
    await pipeline.start()

    abandoned = asyncio.create_task(pipeline.handle_transcript("Count from one to ten"))
    await asyncio.wait_for(orchestrator.first_started.wait(), timeout=1)
    await pipeline.handle_transcript("What model are you?")
    await asyncio.wait_for(abandoned, timeout=1)

    second_packets = orchestrator.requests[1]["packets"]
    assert isinstance(second_packets, list)
    included = [packet["content"] for packet in second_packets if packet["include"]]
    assert included == ["What model are you?"]
    replay = await store.replay("s1")
    assert "Count from one to ten" in [item.content.inline_text for item in replay]
    assert "One. " in [item.content.inline_text for item in replay]
    dispositions = [
        item.metadata.get(TURN_DISPOSITION_METADATA)
        for item in replay
        if TURN_DISPOSITION_METADATA in item.metadata
    ]
    assert dispositions == ["superseded", "completed"]
    packets = context_replay(replay)
    assert not any(
        packet.include and packet.content in {"Count from one to ten", "One. "}
        for packet in packets
    )
    assert pipeline.engine.state is ConversationState.LISTENING
    assert publisher.flushed
    partial_messages = [
        item
        for item in events.messages
        if item.source is SourceKind.MODEL and item.content.inline_text == "One. "
    ]
    assert len(partial_messages) == 1
    assert partial_messages[0].metadata["radcliffe.partial"] == "true"
    assert partial_messages[0].metadata["radcliffe.rendered_chars"] == "5"


@pytest.mark.asyncio
async def test_repeated_cancellation_races_never_reinclude_abandoned_prompts(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    orchestrator = RepeatingInterruptOrchestrator()
    pipeline = VoicePipeline(
        "s1",
        store,
        orchestrator,
        FakeSynthesizer(),
        FakePublisher(),
    )
    await pipeline.start()

    for index in range(10):
        abandoned_text = f"abandon-{index}"
        abandoned = asyncio.create_task(pipeline.handle_transcript(abandoned_text))
        assert await asyncio.wait_for(orchestrator.started.get(), timeout=1) == abandoned_text
        await pipeline.handle_transcript(f"replacement-{index}")
        await asyncio.wait_for(abandoned, timeout=1)

        latest_packets = orchestrator.requests[-1]["packets"]
        assert isinstance(latest_packets, list)
        included = [packet["content"] for packet in latest_packets if packet["include"]]
        assert abandoned_text not in included
        assert f"partial-{abandoned_text}. " not in included

    replay = await store.replay("s1")
    dispositions = [
        item.metadata.get(TURN_DISPOSITION_METADATA)
        for item in replay
        if TURN_DISPOSITION_METADATA in item.metadata
    ]
    assert dispositions.count("superseded") == 10
    assert dispositions.count("completed") == 10
    assert pipeline.engine.state is ConversationState.LISTENING


@pytest.mark.asyncio
async def test_failed_tts_publishes_heard_partial_and_next_turn_recovers(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    publisher = FakePublisher()
    events = FakeEvents()
    pipeline = VoicePipeline(
        "s1",
        store,
        RecoveringOrchestrator(),
        FailSecondSynthesis(),
        publisher,
        events,
    )
    await pipeline.start()

    with pytest.raises(RuntimeError, match="deterministic synthesis failure"):
        await pipeline.handle_transcript("Give me a cosmic response")

    assert pipeline.engine.state is ConversationState.LISTENING
    assert publisher.flushed
    replay = await store.replay("s1")
    model_messages = [item for item in replay if item.source is SourceKind.MODEL]
    assert [item.content.inline_text for item in model_messages] == ["Audible first. "]
    assert model_messages[0].metadata["radcliffe.partial"] == "true"
    assert model_messages[0].metadata["radcliffe.rendered_chars"] == "15"
    published_models = [item for item in events.messages if item.source is SourceKind.MODEL]
    assert published_models == model_messages
    assert [
        item.metadata.get(TURN_DISPOSITION_METADATA)
        for item in replay
        if TURN_DISPOSITION_METADATA in item.metadata
    ] == ["failed"]
    assert not any(
        packet.include and packet.content in {"Give me a cosmic response", "Audible first. "}
        for packet in context_replay(replay)
    )

    await pipeline.handle_transcript("Try another turn")

    assert pipeline.engine.state is ConversationState.LISTENING
    replay = await store.replay("s1")
    assert [item.content.inline_text for item in replay if item.source is SourceKind.MODEL] == [
        "Audible first. ",
        "Recovered.",
    ]
    assert [
        item.metadata.get(TURN_DISPOSITION_METADATA)
        for item in replay
        if TURN_DISPOSITION_METADATA in item.metadata
    ] == ["failed", "completed"]


@pytest.mark.asyncio
async def test_failed_publish_closes_the_playback_audit_and_recovers(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    publisher = FailFirstPublisher()
    pipeline = VoicePipeline(
        "s1",
        store,
        AnswerOrchestrator(),
        FakeSynthesizer(),
        publisher,
    )
    await pipeline.start()

    with pytest.raises(RuntimeError, match="deterministic publish failure"):
        await pipeline.handle_transcript("Fail this playback")

    assert pipeline.engine.state is ConversationState.LISTENING
    assert publisher.flushed
    with sqlite3.connect(store.path) as database:
        statuses = [
            row[0]
            for row in database.execute(
                "SELECT status FROM playback_acknowledgements ORDER BY rowid"
            )
        ]
    assert statuses == ["started", "failed"]


@pytest.mark.asyncio
async def test_punctuation_poor_output_streams_before_model_completion(tmp_path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    orchestrator = PunctuationPoorOrchestrator()
    tts = FakeSynthesizer()
    publisher = FakePublisher()
    pipeline = VoicePipeline(
        "s1",
        store,
        orchestrator,
        tts,
        publisher,
        model_chunk_chars=40,
    )
    await pipeline.start()

    turn = asyncio.create_task(pipeline.handle_transcript("Make an emoji mandala"))
    await asyncio.wait_for(publisher.published.wait(), timeout=1)
    await asyncio.wait_for(orchestrator.waiting.wait(), timeout=1)

    assert not turn.done()
    assert publisher.audio
    orchestrator.release.set()
    await asyncio.wait_for(turn, timeout=1)

    assert "".join(tts.inputs) == orchestrator.text + "Done."
    assert len(publisher.segment_ids) == len(set(publisher.segment_ids))
    assert all(len(chunk) <= 40 for chunk in tts.inputs[:-1])
