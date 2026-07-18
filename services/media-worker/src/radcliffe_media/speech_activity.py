from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer

from radcliffe_media.speech import pcm_to_16khz_mono


@dataclass(frozen=True, slots=True)
class SpeechActivityEvidence:
    window_count: int
    speech_window_count: int
    maximum_probability: float
    average_probability: float


class SpeechActivityAnalyzer(Protocol):
    async def analyze(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> SpeechActivityEvidence: ...


class SileroSpeechActivityAnalyzer:
    def __init__(self, *, speech_probability: float = 0.5) -> None:
        if not 0 < speech_probability < 1:
            raise ValueError("speech probability must be between zero and one")
        self.speech_probability = speech_probability

    async def analyze(self, pcm: bytes, sample_rate: int, channels: int) -> SpeechActivityEvidence:
        return await asyncio.to_thread(self._analyze, pcm, sample_rate, channels)

    def _analyze(self, pcm: bytes, sample_rate: int, channels: int) -> SpeechActivityEvidence:
        waveform = pcm_to_16khz_mono(pcm, sample_rate, channels)
        samples = (np.clip(waveform, -1, 1) * 32767).astype("<i2")
        analyzer = SileroVADAnalyzer()
        analyzer.set_sample_rate(16000)
        probabilities: list[float] = []
        window_samples = analyzer.num_frames_required()
        for start in range(0, len(samples) - window_samples + 1, window_samples):
            confidence = analyzer.voice_confidence(
                samples[start : start + window_samples].tobytes()
            )
            probabilities.append(float(np.asarray(confidence).reshape(-1)[0]))
        return SpeechActivityEvidence(
            window_count=len(probabilities),
            speech_window_count=sum(
                probability >= self.speech_probability for probability in probabilities
            ),
            maximum_probability=max(probabilities, default=0.0),
            average_probability=(sum(probabilities) / len(probabilities) if probabilities else 0.0),
        )
