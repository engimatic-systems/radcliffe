"""Kokoro speech, replacing the macOS `say` voice.

rad-sjwv. These exercise the adapter around the model - rate passthrough, language
derivation, audio conversion - with a stub in place of the weights, so they stay fast and
deterministic. Real synthesis was verified separately against the downloaded model.
"""

from __future__ import annotations

import numpy as np
import pytest
from radcliffe_media.production import speech_provider_from_environment
from radcliffe_media.speech import (
    KOKORO_SAMPLE_RATE,
    KokoroSpeechProvider,
    LocalAppleSpeechProvider,
    ParakeetAppleSpeechProvider,
    pcm_from_waveform,
)


class StubSegment:
    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio


class StubKokoro:
    """Stands in for the loaded model, recording exactly what the adapter asked for."""

    def __init__(self, chunks: int = 1) -> None:
        self.calls: list[dict[str, object]] = []
        self.chunks = chunks

    def generate(
        self,
        *,
        text: str,
        voice: str,
        speed: float,
        lang_code: str,
        verbose: bool,
    ):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang_code": lang_code})
        for index in range(self.chunks):
            yield StubSegment(np.full(4, 0.5 * (index + 1), dtype=np.float32))


def provider_with_stub(stub: StubKokoro, **kwargs) -> KokoroSpeechProvider:
    provider = KokoroSpeechProvider(**kwargs)
    provider._kokoro = stub
    return provider


@pytest.mark.parametrize(
    ("voice", "expected"),
    [("bf_emma", "b"), ("bf_isabella", "b"), ("af_heart", "a"), ("am_michael", "a")],
)
def test_language_is_derived_from_the_voice_name(voice: str, expected: str) -> None:
    """Kokoro names voices <language><gender>_<name>; pinning one would mispronounce the rest."""

    assert KokoroSpeechProvider(kokoro_voice=voice).language_code == expected


@pytest.mark.asyncio
async def test_the_requested_rate_reaches_kokoro_as_its_own_speed_control() -> None:
    """Rate is native, not a resample, which is what keeps fast reading intelligible."""

    stub = StubKokoro()
    provider = provider_with_stub(stub)

    await provider.synthesize("Hello there.", rate=1.5)

    assert stub.calls == [
        {"text": "Hello there.", "voice": "bf_emma", "speed": 1.5, "lang_code": "b"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [0.5, 1.75, 2.0, float("nan")])
async def test_rates_outside_the_supported_range_are_refused(rate: float) -> None:
    provider = provider_with_stub(StubKokoro())

    with pytest.raises(ValueError, match="between 0.75x and 1.5x"):
        await provider.synthesize("Hello.", rate=rate)


@pytest.mark.asyncio
async def test_blank_text_synthesizes_to_silence_rather_than_raising() -> None:
    """Kokoro asserts on empty input; a normalized-away segment must not crash the reader."""

    stub = StubKokoro()
    provider = provider_with_stub(stub)

    assert await provider.synthesize("   \n ") == b""
    assert stub.calls == [], "blank text should never reach the model"


@pytest.mark.asyncio
async def test_a_split_utterance_is_joined_into_one_stretch_of_audio() -> None:
    """Long text is split internally; the pieces are consecutive audio for one sentence."""

    provider = provider_with_stub(StubKokoro(chunks=3))

    pcm = await provider.synthesize("Some longer text.")

    assert len(pcm) == 3 * 4 * 2, "chunks were dropped or padded instead of concatenated"


def test_audio_conversion_matches_what_the_publisher_consumes() -> None:
    """24 kHz mono little-endian 16-bit, the same format the say-based provider produced."""

    pcm = pcm_from_waveform(np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32))
    samples = np.frombuffer(pcm, dtype="<i2")

    assert samples.tolist() == [0, 32767, -32767, 16383]
    assert len(pcm) == 4 * 2


def test_conversion_clips_rather_than_wrapping_around() -> None:
    """Wrapping would turn a loud sample into the opposite polarity - an audible click."""

    samples = np.frombuffer(pcm_from_waveform(np.array([2.5, -2.5], dtype=np.float32)), dtype="<i2")

    assert samples.tolist() == [32767, -32767]


def test_kokoro_is_the_default_spoken_output(monkeypatch) -> None:
    monkeypatch.delenv("RADCLIFFE_SPEECH_PROVIDER", raising=False)

    provider = speech_provider_from_environment()

    assert isinstance(provider, KokoroSpeechProvider)
    assert provider.kokoro_voice == "bf_emma"
    assert provider.supports_speech_rate is True


def test_the_voice_and_model_are_environment_tunable(monkeypatch) -> None:
    monkeypatch.setenv("RADCLIFFE_SPEECH_PROVIDER", "kokoro")
    monkeypatch.setenv("RADCLIFFE_KOKORO_VOICE", "af_heart")
    monkeypatch.setenv("RADCLIFFE_KOKORO_MODEL", "test-kokoro")

    provider = speech_provider_from_environment()

    assert isinstance(provider, KokoroSpeechProvider)
    assert provider.kokoro_voice == "af_heart"
    assert provider.kokoro_model == "test-kokoro"
    assert provider.language_code == "a", "language must follow the overridden voice"


@pytest.mark.parametrize("provider_name", ["local", "parakeet"])
def test_the_say_based_path_remains_available_as_a_rollback(
    monkeypatch, provider_name: str
) -> None:
    monkeypatch.setenv("RADCLIFFE_SPEECH_PROVIDER", provider_name)

    provider = speech_provider_from_environment()

    assert type(provider) is ParakeetAppleSpeechProvider
    assert provider.voice == "Samantha"


def test_whisper_rollback_is_untouched(monkeypatch) -> None:
    monkeypatch.setenv("RADCLIFFE_SPEECH_PROVIDER", "whisper")

    assert type(speech_provider_from_environment()) is LocalAppleSpeechProvider


def test_kokoro_reports_both_models_in_its_identity() -> None:
    """Readiness reporting should name what actually loaded, not just the recognizer."""

    provider = KokoroSpeechProvider(parakeet_model="p", kokoro_model="k", kokoro_voice="bf_emma")

    assert provider.model_identity == "p+k:bf_emma"


def test_the_sample_rate_matches_the_publisher_contract() -> None:
    assert KOKORO_SAMPLE_RATE == 24000
