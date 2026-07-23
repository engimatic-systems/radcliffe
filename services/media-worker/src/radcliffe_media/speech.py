from __future__ import annotations

import asyncio
import io
import math
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import httpx
import mlx.core as mx  # type: ignore[import-not-found]
import mlx_whisper  # type: ignore[import-untyped]
import numpy as np
from parakeet_mlx import from_pretrained as load_parakeet_model  # type: ignore[import-untyped]
from parakeet_mlx.alignment import (  # type: ignore[import-untyped]
    merge_longest_common_subsequence,
    merge_longest_contiguous,
    sentences_to_result,
    tokens_to_sentences,
)
from parakeet_mlx.audio import get_logmel as get_parakeet_logmel  # type: ignore[import-untyped]

DEFAULT_PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
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
    alternative_texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptionAssessment:
    accepted: bool
    reason: str
    segment_count: int
    average_log_probability: float | None
    maximum_no_speech_probability: float | None
    maximum_compression_ratio: float | None
    maximum_hypothesis_word_error_rate: float | None


@runtime_checkable
class DetailedTranscriber(Protocol):
    async def transcribe_with_evidence(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> Transcription: ...


@runtime_checkable
class PreparableSpeechProvider(Protocol):
    model_identity: str

    async def prepare(self) -> None: ...


class SpeechProvider(Protocol):
    supports_speech_rate: bool

    async def transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> str: ...

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes: ...


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


class _ParakeetToken(Protocol):
    id: int
    text: str
    start: float
    duration: float
    end: float
    confidence: float


class _ParakeetSentence(Protocol):
    tokens: list[_ParakeetToken]


class _ParakeetResult(Protocol):
    text: str
    sentences: list[_ParakeetSentence]

    @property
    def tokens(self) -> list[_ParakeetToken]: ...


class _ParakeetPreprocessor(Protocol):
    sample_rate: int
    hop_length: int


class _ParakeetModel(Protocol):
    preprocessor_config: _ParakeetPreprocessor

    def generate(self, mel: object) -> list[_ParakeetResult]: ...


class LocalAppleSpeechProvider:
    supports_speech_rate = True

    def __init__(
        self,
        *,
        whisper_model: str = DEFAULT_WHISPER_MODEL,
        language: str = "en",
        voice: str = "Samantha",
        initial_prompt: str | None = DEFAULT_WHISPER_INITIAL_PROMPT,
        verification_model: str | None = None,
    ) -> None:
        self.whisper_model = whisper_model
        self.language = language
        self.voice = voice
        self.initial_prompt = initial_prompt
        self.verification_model = verification_model

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
        alternatives: tuple[str, ...] = ()
        if self.verification_model:
            alternative = await self._transcribe_result(
                self._waveform(pcm, sample_rate, channels),
                self.verification_model,
                self.initial_prompt,
            )
            alternative_text = str(alternative.get("text", "")).strip()
            if alternative_text:
                alternatives = (alternative_text,)
        return Transcription(
            str(result.get("text", "")).strip(),
            tuple(segments),
            alternatives,
        )

    async def transcribe_result(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> dict[str, object]:
        audio = self._waveform(pcm, sample_rate, channels)
        return await self._transcribe_result(audio, self.whisper_model, self.initial_prompt)

    async def _transcribe_result(
        self,
        audio: np.ndarray,
        model: str,
        initial_prompt: str | None,
    ) -> dict[str, object]:
        options: dict[str, object] = {
            "path_or_hf_repo": model,
            "language": self.language,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.7,
            "temperature": 0.0,
        }
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
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

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes:
        if not math.isfinite(rate) or not 0.75 <= rate <= 1.5:
            raise ValueError("local TTS rate must be between 0.75x and 1.5x")
        with tempfile.TemporaryDirectory(prefix="radcliffe-tts-") as directory:
            aiff_path = Path(directory) / "speech.aiff"
            pcm_path = Path(directory) / "speech.pcm"
            input_path = Path(directory) / "speech.txt"
            input_path.write_text(canonical_text, encoding="utf-8")
            say = await asyncio.create_subprocess_exec(
                "/usr/bin/say",
                "-v",
                self.voice,
                "-r",
                str(round(180 * rate)),
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


class ParakeetAppleSpeechProvider(LocalAppleSpeechProvider):
    """Apple-local Parakeet STT with the existing offline macOS TTS path."""

    def __init__(
        self,
        *,
        parakeet_model: str = DEFAULT_PARAKEET_MODEL,
        voice: str = "Samantha",
        chunk_duration: float = 120.0,
        overlap_duration: float = 15.0,
    ) -> None:
        if chunk_duration <= 0:
            raise ValueError("Parakeet chunk duration must be positive")
        if not 0 <= overlap_duration < chunk_duration:
            raise ValueError("Parakeet overlap must be non-negative and shorter than a chunk")
        super().__init__(voice=voice, initial_prompt=None)
        self.parakeet_model = parakeet_model
        self.model_identity = parakeet_model
        self.chunk_duration = chunk_duration
        self.overlap_duration = overlap_duration
        self._parakeet: _ParakeetModel | None = None
        self._inference_lock = threading.Lock()
        self._warmed = False

    async def prepare(self) -> None:
        """Load weights and exercise inference before the worker reports input readiness."""

        await asyncio.to_thread(self._prepare_sync)

    async def transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> str:
        return (await self.transcribe_with_evidence(pcm, sample_rate, channels)).text

    async def transcribe_with_evidence(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> Transcription:
        waveform = pcm_to_16khz_mono(pcm, sample_rate, channels)
        result = await asyncio.to_thread(self._transcribe_sync, waveform)
        evidence = tuple(self._sentence_evidence(sentence) for sentence in result.sentences)
        return Transcription(result.text.strip(), evidence)

    def _prepare_sync(self) -> None:
        with self._inference_lock:
            if self._warmed:
                return
            self._transcribe_locked(np.zeros(16000, dtype=np.float32))
            self._warmed = True

    def _transcribe_sync(self, waveform: np.ndarray) -> _ParakeetResult:
        with self._inference_lock:
            result = self._transcribe_locked(waveform)
            self._warmed = True
            return result

    def _transcribe_locked(self, waveform: np.ndarray) -> _ParakeetResult:
        if self._parakeet is None:
            self._parakeet = cast(_ParakeetModel, load_parakeet_model(self.parakeet_model))
        sample_rate = self._parakeet.preprocessor_config.sample_rate
        if sample_rate != 16000:
            raise RuntimeError(f"Parakeet model requires unexpected sample rate: {sample_rate}")
        if waveform.size / sample_rate <= self.chunk_duration:
            return self._generate_locked(waveform)

        chunk_samples = int(self.chunk_duration * sample_rate)
        overlap_samples = int(self.overlap_duration * sample_rate)
        tokens: list[_ParakeetToken] = []
        for start in range(0, waveform.size, chunk_samples - overlap_samples):
            end = min(start + chunk_samples, waveform.size)
            if end - start < self._parakeet.preprocessor_config.hop_length:
                break
            chunk = self._generate_locked(waveform[start:end])
            offset = start / sample_rate
            for token in chunk.tokens:
                token.start += offset
                token.end = token.start + token.duration
            if not tokens:
                tokens = chunk.tokens
                continue
            try:
                tokens = cast(
                    list[_ParakeetToken],
                    merge_longest_contiguous(
                        tokens,
                        chunk.tokens,
                        overlap_duration=self.overlap_duration,
                    ),
                )
            except RuntimeError:
                tokens = cast(
                    list[_ParakeetToken],
                    merge_longest_common_subsequence(
                        tokens,
                        chunk.tokens,
                        overlap_duration=self.overlap_duration,
                    ),
                )
        return cast(_ParakeetResult, sentences_to_result(tokens_to_sentences(tokens)))

    def _generate_locked(self, waveform: np.ndarray) -> _ParakeetResult:
        if self._parakeet is None:
            raise RuntimeError("Parakeet model is not loaded")
        mel = get_parakeet_logmel(mx.array(waveform), self._parakeet.preprocessor_config)
        results = self._parakeet.generate(mel)
        if not results:
            raise RuntimeError("Parakeet produced no transcription result")
        return results[0]

    @staticmethod
    def _sentence_evidence(sentence: _ParakeetSentence) -> TranscriptionSegmentEvidence:
        log_confidences: list[float] = []
        for token in sentence.tokens:
            confidence = float(token.confidence)
            if not math.isfinite(confidence) or confidence <= 0:
                log_confidences.append(float("nan"))
            else:
                log_confidences.append(math.log(min(confidence, 1.0)))
        average_log_confidence = (
            sum(log_confidences) / len(log_confidences) if log_confidences else None
        )
        return TranscriptionSegmentEvidence(
            average_log_probability=average_log_confidence,
            no_speech_probability=None,
            compression_ratio=None,
        )


class OpenAISpeechProvider:
    supports_speech_rate = True

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

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes:
        if not math.isfinite(rate) or not 0.75 <= rate <= 1.5:
            raise ValueError("OpenAI TTS rate must be between 0.75x and 1.5x")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.tts_model,
                    "voice": self.voice,
                    "input": canonical_text,
                    "speed": rate,
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
    maximum_hypothesis_word_error_rate: float = 0.15,
) -> TranscriptionAssessment:
    from radcliffe_media.speech_quality import word_error_rate

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
    hypothesis_error_rates = tuple(
        max(
            word_error_rate(transcription.text, alternative),
            word_error_rate(alternative, transcription.text),
        )
        for alternative in transcription.alternative_texts
        if alternative.strip()
    )
    observed_hypothesis_error_rate = max(hypothesis_error_rates) if hypothesis_error_rates else None

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
    elif (
        observed_hypothesis_error_rate is not None
        and observed_hypothesis_error_rate > maximum_hypothesis_word_error_rate
    ):
        reason = "decoder_disagreement"
    return TranscriptionAssessment(
        accepted=reason == "accepted",
        reason=reason,
        segment_count=len(segments),
        average_log_probability=average_log_probability,
        maximum_no_speech_probability=observed_no_speech_probability,
        maximum_compression_ratio=observed_compression_ratio,
        maximum_hypothesis_word_error_rate=observed_hypothesis_error_rate,
    )
