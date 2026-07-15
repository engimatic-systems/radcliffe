from __future__ import annotations

import asyncio
import io
import math
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import httpx
import mlx_whisper  # type: ignore[import-untyped]
import numpy as np

DEFAULT_WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_WHISPER_INITIAL_PROMPT = (
    "Radcliffe vocabulary: dash, bold, Markdown, Cosmic touch, voice controls work, "
    "read source, metadata."
)


@dataclass(frozen=True, slots=True)
class TranscriptionSegmentEvidence:
    average_log_probability: float | None
    no_speech_probability: float | None
    compression_ratio: float | None


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    segments: tuple[TranscriptionSegmentEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptionAssessment:
    accepted: bool
    reason: str
    segment_count: int
    average_log_probability: float | None
    maximum_no_speech_probability: float | None
    maximum_compression_ratio: float | None


@runtime_checkable
class DetailedTranscriber(Protocol):
    async def transcribe_with_evidence(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> Transcription: ...


class SpeechProvider(Protocol):
    async def transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> str: ...

    async def synthesize(self, canonical_text: str) -> bytes: ...


def pcm_to_16khz_mono(pcm: bytes, sample_rate: int, channels: int) -> np.ndarray:
    if sample_rate <= 0 or channels <= 0:
        raise ValueError("sample rate and channel count must be positive")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    complete_samples = samples.size - (samples.size % channels)
    samples = samples[:complete_samples]
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples /= 32768.0
    if sample_rate != 16000 and samples.size:
        output_size = max(1, round(samples.size * 16000 / sample_rate))
        source = np.arange(samples.size, dtype=np.float64)
        target = np.linspace(0, samples.size - 1, output_size)
        samples = np.interp(target, source, samples).astype(np.float32)
    return samples


class LocalAppleSpeechProvider:
    def __init__(
        self,
        *,
        whisper_model: str = DEFAULT_WHISPER_MODEL,
        language: str = "en",
        voice: str = "Samantha",
        initial_prompt: str | None = DEFAULT_WHISPER_INITIAL_PROMPT,
    ) -> None:
        self.whisper_model = whisper_model
        self.language = language
        self.voice = voice
        self.initial_prompt = initial_prompt

    async def transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> str:
        return (await self.transcribe_with_evidence(pcm, sample_rate, channels)).text

    async def transcribe_with_evidence(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> Transcription:
        result = await self.transcribe_result(pcm, sample_rate, channels)
        segments: list[TranscriptionSegmentEvidence] = []
        raw_segments = result.get("segments")
        if isinstance(raw_segments, list):
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    continue
                segments.append(
                    TranscriptionSegmentEvidence(
                        average_log_probability=self._optional_float(
                            raw_segment.get("avg_logprob")
                        ),
                        no_speech_probability=self._optional_float(
                            raw_segment.get("no_speech_prob")
                        ),
                        compression_ratio=self._optional_float(
                            raw_segment.get("compression_ratio")
                        ),
                    )
                )
        return Transcription(str(result.get("text", "")).strip(), tuple(segments))

    async def transcribe_result(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> dict[str, object]:
        audio = self._waveform(pcm, sample_rate, channels)
        options: dict[str, object] = {
            "path_or_hf_repo": self.whisper_model,
            "language": self.language,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.7,
        }
        if self.initial_prompt:
            options["initial_prompt"] = self.initial_prompt
        result = await asyncio.to_thread(mlx_whisper.transcribe, audio, **options)
        return cast(dict[str, object], result)

    @staticmethod
    def _waveform(pcm: bytes, sample_rate: int, channels: int) -> np.ndarray:
        return pcm_to_16khz_mono(pcm, sample_rate, channels)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if isinstance(value, int | float):
            return float(value)
        return None

    async def synthesize(self, canonical_text: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="radcliffe-tts-") as directory:
            aiff_path = Path(directory) / "speech.aiff"
            pcm_path = Path(directory) / "speech.pcm"
            input_path = Path(directory) / "speech.txt"
            input_path.write_text(canonical_text, encoding="utf-8")
            say = await asyncio.create_subprocess_exec(
                "/usr/bin/say",
                "-v",
                self.voice,
                "-o",
                str(aiff_path),
                "-f",
                str(input_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, say_error = await say.communicate()
            if say.returncode:
                raise RuntimeError(f"local TTS failed: {say_error.decode().strip()}")
            ffmpeg = await asyncio.create_subprocess_exec(
                "/opt/homebrew/bin/ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(aiff_path),
                "-f",
                "s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                str(pcm_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, ffmpeg_error = await ffmpeg.communicate()
            if ffmpeg.returncode:
                raise RuntimeError(f"TTS conversion failed: {ffmpeg_error.decode().strip()}")
            return pcm_path.read_bytes()


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


def assess_transcription(
    transcription: Transcription,
    *,
    minimum_average_log_probability: float = -1.5,
    maximum_no_speech_probability: float = 0.6,
    maximum_compression_ratio: float = 2.4,
) -> TranscriptionAssessment:
    segments = transcription.segments
    log_probabilities = tuple(
        segment.average_log_probability
        for segment in segments
        if segment.average_log_probability is not None
    )
    no_speech_probabilities = tuple(
        segment.no_speech_probability
        for segment in segments
        if segment.no_speech_probability is not None
    )
    compression_ratios = tuple(
        segment.compression_ratio for segment in segments if segment.compression_ratio is not None
    )
    average_log_probability = (
        sum(log_probabilities) / len(log_probabilities) if log_probabilities else None
    )
    observed_no_speech_probability = (
        max(no_speech_probabilities) if no_speech_probabilities else None
    )
    observed_compression_ratio = max(compression_ratios) if compression_ratios else None

    reason = "accepted"
    if not transcription.text:
        reason = "empty_transcript"
    elif any(not math.isfinite(value) for value in (*log_probabilities, *compression_ratios)):
        reason = "non_finite_decoder_evidence"
    elif (
        observed_compression_ratio is not None
        and observed_compression_ratio > maximum_compression_ratio
    ):
        reason = "repetitive_decode"
    elif (
        average_log_probability is not None
        and average_log_probability < minimum_average_log_probability
    ):
        reason = "low_log_probability"
    elif no_speech_probabilities and all(
        value >= maximum_no_speech_probability for value in no_speech_probabilities
    ):
        reason = "no_speech_probability"
    return TranscriptionAssessment(
        accepted=reason == "accepted",
        reason=reason,
        segment_count=len(segments),
        average_log_probability=average_log_probability,
        maximum_no_speech_probability=observed_no_speech_probability,
        maximum_compression_ratio=observed_compression_ratio,
    )
