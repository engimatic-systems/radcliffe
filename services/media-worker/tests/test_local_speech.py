import sys

import pytest
from radcliffe_media.speech import LocalAppleSpeechProvider


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS local voice")
async def test_local_tts_produces_24khz_mono_pcm() -> None:
    provider = LocalAppleSpeechProvider(voice="Samantha")

    audio = await provider.synthesize("Radcliffe local speech works.")

    assert len(audio) > 2400
    assert len(audio) % 2 == 0


@pytest.mark.asyncio
async def test_local_whisper_receives_a_private_temporary_wav(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_transcribe(path: str, **options):
        with open(path, "rb") as stream:
            seen["header"] = stream.read(4)
        seen["options"] = options
        return {"text": " local transcript "}

    monkeypatch.setattr("radcliffe_media.speech.mlx_whisper.transcribe", fake_transcribe)
    provider = LocalAppleSpeechProvider(whisper_model="local-test-model")

    transcript = await provider.transcribe(b"\x00\x00" * 1600, 16000, 1)

    assert transcript == "local transcript"
    assert seen["header"] == b"RIFF"
    assert seen["options"] == {
        "path_or_hf_repo": "local-test-model",
        "language": "en",
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.7,
    }
