from __future__ import annotations

import io
import wave

import httpx


class OpenAISpeechProvider:
    def __init__(
        self,
        api_key: str,
        *,
        stt_model: str = "gpt-4o-mini-transcribe",
        tts_model: str = "tts-1",
        voice: str = "alloy",
    ) -> None:
        self.api_key = api_key
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.voice = voice

    async def transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> str:
        wav = io.BytesIO()
        with wave.open(wav, "wb") as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(pcm)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={"model": self.stt_model},
                files={"file": ("turn.wav", wav.getvalue(), "audio/wav")},
            )
            response.raise_for_status()
            return str(response.json()["text"]).strip()

    async def synthesize(self, canonical_text: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.tts_model,
                    "voice": self.voice,
                    "input": canonical_text,
                    "response_format": "pcm",
                },
            )
            response.raise_for_status()
            return response.content
