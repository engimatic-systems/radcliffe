from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_domain.envelope import SourceKind
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
        self.flushed = False

    async def publish(self, _segment_id: str, pcm_audio: bytes) -> int:
        self.audio.append(pcm_audio)
        return len(pcm_audio)

    async def flush(self) -> None:
        self.flushed = True


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
