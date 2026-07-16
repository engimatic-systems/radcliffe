import asyncio
from array import array

import pytest
from pipecat.frames.frames import UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from radcliffe_media.production import EndpointProcessor, EndpointSettings
from radcliffe_media.speech import Transcription, TranscriptionSegmentEvidence
from radcliffe_media.speech_activity import SpeechActivityEvidence


class FakeSpeech:
    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.transcribed = asyncio.Event()

    async def transcribe(self, pcm: bytes, _sample_rate: int, _channels: int) -> str:
        self.calls.append(pcm)
        self.transcribed.set()
        return f"transcript-{len(self.calls)}"


class DetailedFakeSpeech(FakeSpeech):
    def __init__(self, transcription: Transcription) -> None:
        super().__init__()
        self.transcription = transcription

    async def transcribe_with_evidence(
        self, pcm: bytes, _sample_rate: int, _channels: int
    ) -> Transcription:
        self.calls.append(pcm)
        self.transcribed.set()
        return self.transcription


class FakeActivity:
    def __init__(self, *, speech_windows: int, maximum_probability: float) -> None:
        self.speech_windows = speech_windows
        self.maximum_probability = maximum_probability
        self.calls = 0

    async def analyze(
        self, _pcm: bytes, _sample_rate: int, _channels: int
    ) -> SpeechActivityEvidence:
        self.calls += 1
        return SpeechActivityEvidence(
            window_count=50,
            speech_window_count=self.speech_windows,
            maximum_probability=self.maximum_probability,
            average_probability=self.maximum_probability / 2,
        )


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


def audio_frame(*, voiced: bool, amplitude: int | None = None) -> UserAudioRawFrame:
    value = amplitude if amplitude is not None else (3000 if voiced else 0)
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

    await feed(endpoint, voiced=True, frames=5)
    assert voice.active == 1
    await feed(endpoint, voiced=True, frames=1)
    assert voice.active == 0
    await feed(endpoint, voiced=True, frames=9)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.sleep(0)

    assert voice.transcripts == ["transcript-1", "transcript-2"]
    assert voice.maximum_active == 1
    assert voice.barge_ins == 1


def test_endpoint_settings_are_configurable_and_reject_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_SILENCE_MS", "1200")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_VOICE_MS", "300")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_RMS", "700")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_PRE_ROLL_MS", "360")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_FOREGROUND_RMS", "1300")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_BARGE_IN_RMS", "1900")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_FOREGROUND_VOICE_MS", "180")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_BARGE_IN_CONFIRMATION_MS", "340")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_BARGE_IN_RELEASE_MS", "140")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS", "20000")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_UNCONFIRMED_MS", "2200")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_MIN_VOICE_RATIO", "0.07")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_SPEECH_WINDOWS", "3")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_SPEECH_PROBABILITY", "0.55")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MIN_AVG_LOGPROB", "-0.9")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MAX_NO_SPEECH_PROBABILITY", "0.65")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MAX_COMPRESSION_RATIO", "2.2")

    assert EndpointSettings.from_environment() == EndpointSettings(
        rms_threshold=700,
        pre_roll_ms=360,
        foreground_rms_threshold=1300,
        barge_in_rms_threshold=1900,
        minimum_voice_ms=300,
        minimum_foreground_voice_ms=180,
        barge_in_confirmation_ms=340,
        barge_in_release_ms=140,
        end_silence_ms=1200,
        maximum_unconfirmed_ms=2200,
        maximum_utterance_ms=20000,
        minimum_maximum_voice_ratio=0.07,
        minimum_speech_windows=3,
        speech_probability=0.55,
        minimum_average_log_probability=-0.9,
        maximum_no_speech_probability=0.65,
        maximum_compression_ratio=2.2,
    )
    with pytest.raises(ValueError, match="positive"):
        EndpointSettings(end_silence_ms=0)
    with pytest.raises(ValueError, match="voice ratio"):
        EndpointSettings(minimum_maximum_voice_ratio=0)
    with pytest.raises(ValueError, match="foreground RMS"):
        EndpointSettings(foreground_rms_threshold=500)


@pytest.mark.asyncio
async def test_endpoint_preserves_quiet_word_onset_in_bounded_pre_roll() -> None:
    speech = FakeSpeech()
    endpoint = EndpointProcessor(speech, EndpointSettings(pre_roll_ms=100))

    for _ in range(8):
        await endpoint.process_frame(
            audio_frame(voiced=False, amplitude=500),
            FrameDirection.DOWNSTREAM,
        )
    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)

    quiet_frame = array("h", [500] * 320).tobytes()
    assert speech.calls[0].startswith(quiet_frame * 5)
    assert len(speech.calls[0]) == (5 + 15 + 45) * 640


@pytest.mark.asyncio
async def test_background_babble_does_not_barge_in_or_become_a_turn() -> None:
    speech = FakeSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(speech)
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(voice.first_started.wait(), timeout=1)

    for _ in range(150):
        await endpoint.process_frame(
            audio_frame(voiced=True, amplitude=850),
            FrameDirection.DOWNSTREAM,
        )
    await feed(endpoint, voiced=False, frames=45)

    assert voice.active == 1
    assert voice.barge_ins == 0
    assert voice.transcripts == ["transcript-1"]
    assert len(speech.calls) == 1

    endpoint.activity = FakeActivity(speech_windows=8, maximum_probability=0.99)
    await feed(endpoint, voiced=True, frames=15)

    assert voice.active == 0
    assert voice.barge_ins == 1


@pytest.mark.asyncio
async def test_loud_non_speech_must_pass_activity_gate_before_barge_in() -> None:
    speech = FakeSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(speech)
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(voice.first_started.wait(), timeout=1)
    endpoint.activity = FakeActivity(speech_windows=0, maximum_probability=0.12)

    await feed(endpoint, voiced=True, frames=30)
    await feed(endpoint, voiced=False, frames=45)

    assert voice.active == 1
    assert voice.barge_ins == 0
    assert voice.transcripts == ["transcript-1"]
    assert len(speech.calls) == 1


@pytest.mark.asyncio
async def test_foreground_endpoint_ignores_continuing_low_level_babble() -> None:
    speech = FakeSpeech()
    voice = FakeVoice()
    endpoint = EndpointProcessor(speech)
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=20)
    for _ in range(150):
        await endpoint.process_frame(
            audio_frame(voiced=True, amplitude=850),
            FrameDirection.DOWNSTREAM,
        )
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)
    await asyncio.sleep(0)

    assert len(speech.calls) == 1
    # 400 ms foreground plus roughly 900 ms of lower-level background, not all 3 seconds.
    assert len(speech.calls[0]) <= 70 * 640
    assert voice.transcripts == ["transcript-1"]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("amplitude", [0, 400])
async def test_digital_silence_and_room_tone_never_start_a_turn(amplitude: int) -> None:
    speech = FakeSpeech()
    endpoint = EndpointProcessor(speech)

    for _ in range(100):
        await endpoint.process_frame(
            audio_frame(voiced=False, amplitude=amplitude),
            FrameDirection.DOWNSTREAM,
        )

    assert endpoint.turn_task is None
    assert speech.calls == []


@pytest.mark.asyncio
async def test_subminimum_noise_burst_resets_after_normal_endpoint_silence() -> None:
    speech = FakeSpeech()
    endpoint = EndpointProcessor(speech)

    await feed(endpoint, voiced=True, frames=5)
    await feed(endpoint, voiced=False, frames=45)

    assert not endpoint.speaking
    assert endpoint.buffer == b""
    assert endpoint.turn_task is None
    assert speech.calls == []


@pytest.mark.asyncio
async def test_thirty_second_sparse_spikes_are_rejected_before_whisper() -> None:
    speech = FakeSpeech()
    endpoint = EndpointProcessor(speech, EndpointSettings(maximum_utterance_ms=30000))

    for index in range(1500):
        await endpoint.process_frame(
            audio_frame(voiced=index % 40 == 0),
            FrameDirection.DOWNSTREAM,
        )

    assert endpoint.turn_task is None
    assert speech.calls == []


@pytest.mark.asyncio
async def test_sparse_playback_like_bursts_are_rejected_by_speech_activity() -> None:
    speech = FakeSpeech()
    voice = FakeVoice()
    activity = FakeActivity(speech_windows=0, maximum_probability=0.08)
    endpoint = EndpointProcessor(speech, activity=activity)
    endpoint.voice = voice  # type: ignore[assignment]

    for index in range(85):
        await endpoint.process_frame(
            audio_frame(voiced=index < 11 or index in range(35, 40)),
            FrameDirection.DOWNSTREAM,
        )
    task = endpoint.turn_task
    assert task is not None
    await task

    assert activity.calls == 1
    assert speech.calls == []
    assert voice.transcripts == []


@pytest.mark.asyncio
async def test_legitimate_short_speech_survives_activity_and_decoder_gates() -> None:
    transcription = Transcription(
        "Stop.",
        (
            TranscriptionSegmentEvidence(
                average_log_probability=-0.75,
                no_speech_probability=0.02,
                compression_ratio=0.4,
            ),
        ),
    )
    speech = DetailedFakeSpeech(transcription)
    voice = FakeVoice()
    activity = FakeActivity(speech_windows=4, maximum_probability=0.98)
    endpoint = EndpointProcessor(speech, activity=activity)
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=13)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)
    task = endpoint.turn_task
    if task is not None:
        await task

    assert voice.transcripts == ["Stop."]


@pytest.mark.asyncio
async def test_repetitive_whisper_hallucination_never_reaches_conversation_pipeline() -> None:
    speech = DetailedFakeSpeech(
        Transcription(
            "available " * 100,
            (
                TranscriptionSegmentEvidence(
                    average_log_probability=-0.4,
                    no_speech_probability=0.01,
                    compression_ratio=58.5,
                ),
            ),
        )
    )
    voice = FakeVoice()
    endpoint = EndpointProcessor(
        speech,
        activity=FakeActivity(speech_windows=4, maximum_probability=0.98),
    )
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)
    task = endpoint.turn_task
    if task is not None:
        await task

    assert voice.transcripts == []
