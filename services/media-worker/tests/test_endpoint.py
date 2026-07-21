import asyncio
from array import array

import pytest
from loguru import logger
from pipecat.frames.frames import UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from radcliffe_media.production import (
    EndpointProcessor,
    EndpointSettings,
    speech_provider_from_environment,
)
from radcliffe_media.speech import (
    LocalAppleSpeechProvider,
    ParakeetAppleSpeechProvider,
    Transcription,
    TranscriptionSegmentEvidence,
)
from radcliffe_media.speech_activity import SpeechActivityEvidence


class FakeSpeech:
    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.transcribed = asyncio.Event()

    async def transcribe(self, pcm: bytes, _sample_rate: int, _channels: int) -> str:
        self.calls.append(pcm)
        self.transcribed.set()
        return f"transcript-{len(self.calls)}"


class DelayedFirstSpeech(FakeSpeech):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def transcribe(self, pcm: bytes, _sample_rate: int, _channels: int) -> str:
        self.calls.append(pcm)
        if len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()
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


class SequencedActivity(FakeActivity):
    def __init__(self, probabilities: tuple[float, ...]) -> None:
        super().__init__(speech_windows=0, maximum_probability=probabilities[-1])
        self.probabilities = probabilities

    async def analyze(
        self, _pcm: bytes, _sample_rate: int, _channels: int
    ) -> SpeechActivityEvidence:
        probability = self.probabilities[min(self.calls, len(self.probabilities) - 1)]
        self.calls += 1
        return SpeechActivityEvidence(
            window_count=50,
            speech_window_count=0,
            maximum_probability=probability,
            average_probability=probability / 2,
        )


class FakeVoice:
    def __init__(self, *, block_first: bool = False) -> None:
        self.block_first = block_first
        self.transcripts: list[str] = []
        self.barge_ins = 0
        self.barge_in_dispositions: list[str] = []
        self.active = 0
        self.maximum_active = 0
        self.first_started = asyncio.Event()
        self.participant_connections = 0
        self.clarifications: list[str] = []
        self.response_tasks: set[asyncio.Task[None]] = set()

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

    async def submit_transcript(self, transcript: str) -> None:
        task = asyncio.create_task(self.handle_transcript(transcript))
        self.response_tasks.add(task)
        task.add_done_callback(self.response_tasks.discard)
        await asyncio.sleep(0)

    async def barge_in(self, **evidence: object) -> None:
        self.barge_ins += 1
        disposition = evidence.get("disposition", "interrupted")
        self.barge_in_dispositions.append(str(disposition))
        tasks = tuple(self.response_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def foreground_turn_active(self) -> bool:
        return self.active > 0

    async def participant_connected(self) -> None:
        self.participant_connections += 1

    async def request_clarification(self, reason: str) -> None:
        self.clarifications.append(reason)


def audio_frame(
    *,
    voiced: bool,
    amplitude: int | None = None,
    user_id: str | None = None,
) -> UserAudioRawFrame:
    value = amplitude if amplitude is not None else (3000 if voiced else 0)
    pcm = array("h", [value] * 320).tobytes()
    return UserAudioRawFrame(pcm, 16000, 1, user_id=user_id)


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
async def test_participant_rejoin_unpauses_and_clears_stale_endpoint_audio() -> None:
    voice = FakeVoice()
    endpoint = EndpointProcessor(FakeSpeech())
    endpoint.voice = voice  # type: ignore[assignment]
    endpoint.paused = True
    endpoint.speaking = True
    endpoint.buffer.extend(b"stale audio")
    endpoint.idle_buffer.extend(b"stale pre-roll")

    await endpoint.participant_connected()

    assert not endpoint.paused
    assert not endpoint.speaking
    assert endpoint.buffer == b""
    assert endpoint.idle_buffer == b""
    assert voice.participant_connections == 1


@pytest.mark.asyncio
async def test_audio_subscription_waits_for_the_first_processed_frame_before_ready() -> None:
    endpoint = EndpointProcessor(FakeSpeech())
    ready: list[str] = []

    async def input_ready(participant_id: str) -> None:
        ready.append(participant_id)

    endpoint.input_ready_handler = input_ready
    endpoint.audio_track_subscribed("participant-1")

    assert ready == []
    await endpoint.process_frame(
        audio_frame(voiced=False, user_id="participant-1"),
        FrameDirection.DOWNSTREAM,
    )

    assert ready == ["participant-1"]
    assert endpoint.idle_buffer != b""


@pytest.mark.asyncio
async def test_new_speech_interrupts_response_without_cancelling_previous_capture() -> None:
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
    task = endpoint.turn_task
    if task is not None:
        await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)

    assert voice.transcripts == ["transcript-1", "transcript-2"]
    assert voice.maximum_active == 1
    assert voice.barge_ins == 1
    assert voice.barge_in_dispositions == ["continued"]


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation_frames", [21, 28])
async def test_phone_timing_continuation_never_cancels_pending_transcription(
    continuation_frames: int,
) -> None:
    speech = DelayedFirstSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(speech, EndpointSettings(end_silence_ms=900))
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.first_started.wait(), timeout=1)

    # The phone resumed 422 ms and 571 ms after the silence endpoint. Keep the
    # first STT pending while the next clause begins, matching those traces.
    await feed(endpoint, voiced=False, frames=continuation_frames)
    await feed(endpoint, voiced=True, frames=3)
    speech.release_first.set()
    await asyncio.wait_for(voice.first_started.wait(), timeout=1)
    await feed(endpoint, voiced=True, frames=3)

    assert voice.barge_ins == 1
    assert voice.barge_in_dispositions == ["continued"]
    assert speech.calls

    await feed(endpoint, voiced=True, frames=9)
    await feed(endpoint, voiced=False, frames=45)
    task = endpoint.turn_task
    if task is not None:
        await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)

    assert len(speech.calls) == 2
    assert voice.transcripts == ["transcript-1", "transcript-2"]
    assert voice.maximum_active == 1


@pytest.mark.asyncio
async def test_completed_continuation_preserves_prior_turn_even_when_stt_is_slower() -> None:
    speech = DelayedFirstSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(speech, EndpointSettings(end_silence_ms=900))
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.first_started.wait(), timeout=1)

    # Let the whole continuation endpoint while the first transcription is
    # still blocked. Its ordered handoff must preserve, not supersede, turn one.
    await feed(endpoint, voiced=False, frames=21)
    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    assert voice.barge_ins == 0

    speech.release_first.set()
    for _ in range(2):
        task = endpoint.turn_task
        if task is not None:
            await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)

    assert len(speech.calls) == 2
    assert voice.transcripts == ["transcript-1", "transcript-2"]
    assert voice.barge_in_dispositions == ["continued"]
    assert voice.maximum_active == 1


def test_endpoint_settings_are_configurable_and_reject_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_SILENCE_MS", "1200")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_VOICE_MS", "300")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_RMS", "700")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_PRE_ROLL_MS", "360")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_FOREGROUND_RMS", "1300")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_BARGE_IN_RMS", "1900")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_RMS", "9000")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_FOREGROUND_VOICE_MS", "180")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_BARGE_IN_CONFIRMATION_MS", "340")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_CONFIRMATION_MS", "500")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_BARGE_IN_RELEASE_MS", "140")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_CONTINUATION_GRACE_MS", "800")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS", "20000")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_UNCONFIRMED_MS", "2200")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MAX_MIN_VOICE_RATIO", "0.07")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_MIN_SPEECH_WINDOWS", "3")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_SPEECH_PROBABILITY", "0.55")
    monkeypatch.setenv("RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_MIN_PROBABILITY", "0.2")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MIN_AVG_LOGPROB", "-0.9")
    monkeypatch.setenv("RADCLIFFE_WHISPER_SHORT_FOREGROUND_MS", "650")
    monkeypatch.setenv("RADCLIFFE_WHISPER_SHORT_MIN_AVG_LOGPROB", "-0.7")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MAX_NO_SPEECH_PROBABILITY", "0.65")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MAX_COMPRESSION_RATIO", "2.2")

    assert EndpointSettings.from_environment() == EndpointSettings(
        rms_threshold=700,
        pre_roll_ms=360,
        foreground_rms_threshold=1300,
        barge_in_rms_threshold=1900,
        strong_barge_in_rms_threshold=9000,
        minimum_voice_ms=300,
        minimum_foreground_voice_ms=180,
        barge_in_confirmation_ms=340,
        strong_barge_in_confirmation_ms=500,
        barge_in_release_ms=140,
        end_silence_ms=1200,
        continuation_grace_ms=800,
        maximum_unconfirmed_ms=2200,
        maximum_utterance_ms=20000,
        minimum_maximum_voice_ratio=0.07,
        minimum_speech_windows=3,
        speech_probability=0.55,
        strong_barge_in_minimum_probability=0.2,
        minimum_average_log_probability=-0.9,
        short_foreground_voice_ms=650,
        short_minimum_average_log_probability=-0.7,
        maximum_no_speech_probability=0.65,
        maximum_compression_ratio=2.2,
    )
    with pytest.raises(ValueError, match="positive"):
        EndpointSettings(end_silence_ms=0)
    with pytest.raises(ValueError, match="voice ratio"):
        EndpointSettings(minimum_maximum_voice_ratio=0)
    with pytest.raises(ValueError, match="foreground RMS"):
        EndpointSettings(foreground_rms_threshold=500)
    with pytest.raises(ValueError, match="short decoder confidence"):
        EndpointSettings(
            minimum_average_log_probability=-0.5,
            short_minimum_average_log_probability=-0.9,
        )


@pytest.mark.parametrize("provider_name", ["local", "parakeet"])
def test_local_speech_profile_defaults_to_parakeet(monkeypatch, provider_name: str) -> None:
    monkeypatch.setenv("RADCLIFFE_SPEECH_PROVIDER", provider_name)
    monkeypatch.setenv("RADCLIFFE_PARAKEET_MODEL", "test-parakeet")
    monkeypatch.setenv("RADCLIFFE_PARAKEET_CHUNK_SECONDS", "90")
    monkeypatch.setenv("RADCLIFFE_PARAKEET_OVERLAP_SECONDS", "10")

    provider = speech_provider_from_environment()

    assert isinstance(provider, ParakeetAppleSpeechProvider)
    assert provider.parakeet_model == "test-parakeet"
    assert provider.chunk_duration == 90
    assert provider.overlap_duration == 10


def test_whisper_remains_an_explicit_rollback_profile(monkeypatch) -> None:
    monkeypatch.setenv("RADCLIFFE_SPEECH_PROVIDER", "whisper")
    monkeypatch.setenv("RADCLIFFE_WHISPER_MODEL", "rollback-whisper")

    provider = speech_provider_from_environment()

    assert type(provider) is LocalAppleSpeechProvider
    assert provider.whisper_model == "rollback-whisper"


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
    assert voice.barge_in_dispositions == ["interrupted"]


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
async def test_retained_high_energy_zero_window_overlap_uses_bounded_fallback() -> None:
    speech = FakeSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(
        speech,
        EndpointSettings(barge_in_confirmation_ms=137.5),
    )
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(voice.first_started.wait(), timeout=1)
    activity = SequencedActivity((0.1565, 0.2015, 0.3709))
    endpoint.activity = activity

    for _ in range(21):
        await endpoint.process_frame(
            audio_frame(voiced=True, amplitude=12517),
            FrameDirection.DOWNSTREAM,
        )

    assert activity.calls == 3
    assert voice.barge_ins == 1
    assert endpoint.barge_in_confirmed
    assert voice.active == 0


@pytest.mark.asyncio
async def test_strong_zero_window_echo_stays_rejected_below_probability_floor() -> None:
    speech = FakeSpeech()
    voice = FakeVoice(block_first=True)
    endpoint = EndpointProcessor(speech)
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=15)
    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(voice.first_started.wait(), timeout=1)
    endpoint.activity = FakeActivity(speech_windows=0, maximum_probability=0.12)

    for _ in range(30):
        await endpoint.process_frame(
            audio_frame(voiced=True, amplitude=12517),
            FrameDirection.DOWNSTREAM,
        )
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
async def test_default_limit_keeps_a_continuous_thirty_two_second_turn_whole() -> None:
    speech = FakeSpeech()
    voice = FakeVoice()
    endpoint = EndpointProcessor(speech)
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=1_600)

    assert endpoint.turn_task is None
    assert speech.calls == []

    await feed(endpoint, voiced=False, frames=45)
    await asyncio.wait_for(speech.transcribed.wait(), timeout=1)
    task = endpoint.turn_task
    if task is not None:
        await task

    assert len(speech.calls) == 1
    assert len(speech.calls[0]) == 1_645 * 640
    assert voice.transcripts == ["transcript-1"]
    assert voice.barge_ins == 0


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
@pytest.mark.parametrize(
    ("foreground_voice_ms", "speech_windows", "maximum_probability", "average_log_probability"),
    [
        (550.0, 6, 0.7162, -1.0959),
        (343.75, 12, 0.914, -0.922),
    ],
)
async def test_retained_silent_thank_you_evidence_never_reaches_the_conversation(
    foreground_voice_ms: float,
    speech_windows: int,
    maximum_probability: float,
    average_log_probability: float,
) -> None:
    speech = DetailedFakeSpeech(
        Transcription(
            "Thank you.",
            (TranscriptionSegmentEvidence(average_log_probability, 0.02, 0.5),),
        )
    )
    voice = FakeVoice()
    endpoint = EndpointProcessor(
        speech,
        activity=FakeActivity(
            speech_windows=speech_windows,
            maximum_probability=maximum_probability,
        ),
    )
    endpoint.voice = voice  # type: ignore[assignment]

    diagnostics: list[str] = []
    sink = logger.add(diagnostics.append, format="{message}")
    try:
        await endpoint._complete(
            array("h", [2500] * 24_000).tobytes(),
            16_000,
            1,
            voice_ms=foreground_voice_ms,
            foreground_voice_ms=foreground_voice_ms,
            voice_ratio=0.4,
            endpoint_reason="silence",
            pre_roll_ms=600,
            trailing_silence_ms=900,
            participant_id="retained-evidence",
        )
    finally:
        logger.remove(sink)

    assert voice.transcripts == []
    assert voice.clarifications == []
    diagnostic = "".join(diagnostics)
    assert "accepted=False reason=low_log_probability" in diagnostic
    assert f"foreground_voice_ms={foreground_voice_ms}" in diagnostic
    assert f"speech_windows={speech_windows}" in diagnostic
    assert "decoder_confidence_floor=-0.9 short_candidate=True" in diagnostic
    assert "Thank you" not in diagnostic


@pytest.mark.asyncio
async def test_deliberately_spoken_thank_you_remains_accepted_as_a_short_turn() -> None:
    speech = DetailedFakeSpeech(
        Transcription(
            "Thank you.",
            (TranscriptionSegmentEvidence(-0.54, 0.01, 0.56),),
        )
    )
    voice = FakeVoice()
    endpoint = EndpointProcessor(
        speech,
        activity=FakeActivity(speech_windows=8, maximum_probability=0.99),
    )
    endpoint.voice = voice  # type: ignore[assignment]

    await endpoint._complete(
        array("h", [3000] * 20_000).tobytes(),
        16_000,
        1,
        voice_ms=420,
        foreground_voice_ms=420,
        voice_ratio=0.5,
        endpoint_reason="silence",
        pre_roll_ms=600,
        trailing_silence_ms=900,
        participant_id="deliberate-thanks",
    )

    assert voice.transcripts == ["Thank you."]
    assert voice.clarifications == []


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


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["low_log_probability", "decoder_disagreement"])
async def test_uncertain_transcript_requests_clarification_without_persisting_user_text(
    reason: str,
) -> None:
    alternative = ("Turn off the kitchen lights.",) if reason == "decoder_disagreement" else ()
    log_probability = -1.7 if reason == "low_log_probability" else -0.4
    speech = DetailedFakeSpeech(
        Transcription(
            "Turn on the kitchen lights.",
            (TranscriptionSegmentEvidence(log_probability, 0.01, 0.5),),
            alternative,
        )
    )
    voice = FakeVoice()
    endpoint = EndpointProcessor(
        speech,
        activity=FakeActivity(speech_windows=4, maximum_probability=0.98),
    )
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=40)
    await feed(endpoint, voiced=False, frames=45)
    task = endpoint.turn_task
    if task is not None:
        await task

    assert voice.transcripts == []
    assert voice.clarifications == [reason]


def test_notification_gate_rejects_candidate_user_speech_and_pause() -> None:
    endpoint = EndpointProcessor(FakeSpeech())

    assert endpoint.foreground_quiet()

    endpoint.speaking = True
    endpoint.buffer.extend(b"candidate user audio")
    assert not endpoint.foreground_quiet()

    endpoint.speaking = False
    endpoint.buffer.clear()
    endpoint.paused = True
    assert not endpoint.foreground_quiet()


@pytest.mark.asyncio
async def test_confirmed_speech_barges_into_voice_owned_out_of_band_audio() -> None:
    voice = FakeVoice()
    voice.active = 1
    endpoint = EndpointProcessor(FakeSpeech())
    endpoint.voice = voice  # type: ignore[assignment]

    await feed(endpoint, voiced=True, frames=7)

    assert voice.barge_ins == 1
    assert endpoint.barge_in_confirmed
