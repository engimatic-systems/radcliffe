import asyncio
from array import array

import pytest
from pipecat.frames.frames import UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from radcliffe_media.production import EndpointProcessor, EndpointSettings


class FakeSpeech:
    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.transcribed = asyncio.Event()

    async def transcribe(self, pcm: bytes, _sample_rate: int, _channels: int) -> str:
        self.calls.append(pcm)
        self.transcribed.set()
        return f"transcript-{len(self.calls)}"


class FakeVoice:
    def __init__(self, *, block_first: bool = False) -> None:
        self.block_first = block_first
        self.transcripts: list[str] = []
        self.barge_ins = 0
        self.active = 0
        self.maximum_active = 0
        self.first_started = asyncio.Event()

    async def handle_transcript(self, transcript: str) -> None:
        self.transcripts.append(transcript)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.block_first and len(self.transcripts) == 1:
                self.first_started.set()
                await asyncio.Event().wait()
        finally:
            self.active -= 1

    async def barge_in(self) -> None:
        self.barge_ins += 1


def audio_frame(*, voiced: bool) -> UserAudioRawFrame:
    value = 1200 if voiced else 0
    pcm = array("h", [value] * 320).tobytes()
    return UserAudioRawFrame(pcm, 16000, 1)


async def feed(endpoint: EndpointProcessor, *, voiced: bool, frames: int) -> None:
    for _ in range(frames):
        await endpoint.process_frame(audio_frame(voiced=voiced), FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_short_natural_pause_stays_inside_one_endpointed_utterance() -> None:
    speech = FakeSpeech()
    voice = FakeVoice()
    endpoint = EndpointProcessor(speech, EndpointSettings(end_silence_ms=900))
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=35)
    assert endpoint.turn_task is None
    await feed(endpoint, voiced=True, frames=5)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)
    await asyncio.sleep(0)

    assert len(speech.calls) == 1
    assert voice.transcripts == ["transcript-1"]
    assert voice.barge_ins == 0


@pytest.mark.asyncio
async def test_new_speech_cancels_and_awaits_the_previous_endpoint_task() -> None:
    speech = FakeSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(speech, EndpointSettings(end_silence_ms=900))
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(voice.first_started.wait(), timeout=1)

    # A deliberate pause longer than the endpoint threshold creates a candidate
    # first turn, but the later clause still has exclusive ownership.
    await feed(endpoint, voiced=False, frames=15)
    assert voice.active == 1

    await feed(endpoint, voiced=True, frames=1)
    assert voice.active == 0
    await feed(endpoint, voiced=True, frames=14)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.sleep(0)

    assert voice.transcripts == ["transcript-1", "transcript-2"]
    assert voice.maximum_active == 1
    assert voice.barge_ins == 1


def test_endpoint_settings_are_configurable_and_reject_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_SILENCE_MS", "1200")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_VOICE_MS", "300")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_RMS", "700")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS", "20000")

    assert EndpointSettings.from_environment() == EndpointSettings(700, 300, 1200, 20000)
    with pytest.raises(ValueError, match="positive"):
        EndpointSettings(end_silence_ms=0)


@pytest.mark.asyncio
async def test_maximum_utterance_bounds_intermittent_noise_that_defeats_silence() -> None:
    speech = FakeSpeech()
    voice = FakeVoice()
    endpoint = EndpointProcessor(
        speech,
        EndpointSettings(
            minimum_voice_ms=250,
            end_silence_ms=900,
            maximum_utterance_ms=2000,
        ),
    )
    endpoint.voice = voice  # type: ignore[assignment]

    for index in range(100):
        await endpoint.process_frame(
            audio_frame(voiced=index < 15 or index in {40, 80}),
            FrameDirection.DOWNSTREAM,
        )
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)
    await asyncio.sleep(0)

    assert len(speech.calls) == 1
    assert len(speech.calls[0]) == 100 * 640
    assert voice.transcripts == ["transcript-1"]
