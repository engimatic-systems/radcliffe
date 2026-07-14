import sys

import numpy as np
import pytest
from radcliffe_media.speech import LocalAppleSpeechProvider


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS local voice")
async def test_local_tts_produces_24khz_mono_pcm() -> None:
    provider = LocalAppleSpeechProvider(voice="Samantha")

    audio = await provider.synthesize("- **Cosmic touch:** Moon 🌙\n-- still canonical text")

    assert len(audio) > 2400
    assert len(audio) % 2 == 0


@pytest.mark.asyncio
async def test_local_whisper_receives_an_in_memory_16khz_mono_waveform(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_transcribe(audio: np.ndarray, **options):
        seen["audio"] = audio
        seen["options"] = options
        return {"text": " local transcript "}

    monkeypatch.setattr("radcliffe_media.speech.mlx_whisper.transcribe", fake_transcribe)
    provider = LocalAppleSpeechProvider(whisper_model="local-test-model")

    stereo_8khz = np.tile(np.array([16384, 16384], dtype="<i2"), 800).tobytes()
    transcript = await provider.transcribe(stereo_8khz, 8000, 2)

    assert transcript == "local transcript"
    audio = seen["audio"]
    assert isinstance(audio, np.ndarray)
    assert audio.shape == (1600,)
    assert audio.dtype == np.float32
    assert np.allclose(audio, 0.5)
    assert seen["options"] == {
        "path_or_hf_repo": "local-test-model",
        "language": "en",
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.7,
    }


def test_local_waveform_rejects_invalid_audio_metadata() -> None:
    with pytest.raises(ValueError, match="positive"):
        LocalAppleSpeechProvider._waveform(b"", 0, 1)
