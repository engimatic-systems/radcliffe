from __future__ import annotations

import asyncio
import math
import os
from array import array
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame, OutputAudioRawFrame, UserAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner
from radcliffe_domain.configuration import ConfigurationRegistry
from radcliffe_domain.jobs import JobStore
from radcliffe_domain.session import PlaybackAcknowledgement
from radcliffe_domain.store import EventStore
from radcliffe_domain.telemetry import TelemetrySink
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

from radcliffe_media.control import ClientControlHandler
from radcliffe_media.events import LiveKitEventSink
from radcliffe_media.livekit_adapter import (
    LiveKitConnection,
    RadcliffeLiveKitTransport,
    create_transport,
)
from radcliffe_media.notifications import JobNotificationScheduler
from radcliffe_media.pipeline import VoicePipeline
from radcliffe_media.session_actions import SessionActionClient
from radcliffe_media.sidecar import SidecarClient
from radcliffe_media.speech import (
    DEFAULT_WHISPER_INITIAL_PROMPT,
    DetailedTranscriber,
    LocalAppleSpeechProvider,
    OpenAISpeechProvider,
    SpeechProvider,
    Transcription,
    assess_transcription,
)
from radcliffe_media.speech_activity import SileroSpeechActivityAnalyzer, SpeechActivityAnalyzer
from radcliffe_media.speech_capture import EndpointCaptureEvidence, SpeechCapture


@dataclass(frozen=True, slots=True)
class EndpointSettings:
    rms_threshold: float = 600
    pre_roll_ms: float = 600
    foreground_rms_threshold: float = 1200
    barge_in_rms_threshold: float = 1800
    strong_barge_in_rms_threshold: float = 8000
    minimum_voice_ms: float = 250
    minimum_foreground_voice_ms: float = 160
    barge_in_confirmation_ms: float = 120
    strong_barge_in_confirmation_ms: float = 400
    barge_in_release_ms: float = 120
    end_silence_ms: float = 900
    maximum_unconfirmed_ms: float = 2000
    maximum_utterance_ms: float = 30000
    minimum_maximum_voice_ratio: float = 0.05
    minimum_speech_windows: int = 2
    speech_probability: float = 0.5
    strong_barge_in_minimum_probability: float = 0.15
    minimum_average_log_probability: float = -1.5
    short_foreground_voice_ms: float = 700
    short_minimum_average_log_probability: float = -0.9
    maximum_no_speech_probability: float = 0.6
    maximum_compression_ratio: float = 2.4

    def __post_init__(self) -> None:
        if (
            min(
                self.rms_threshold,
                self.pre_roll_ms,
                self.foreground_rms_threshold,
                self.barge_in_rms_threshold,
                self.strong_barge_in_rms_threshold,
                self.minimum_voice_ms,
                self.minimum_foreground_voice_ms,
                self.barge_in_confirmation_ms,
                self.strong_barge_in_confirmation_ms,
                self.barge_in_release_ms,
                self.end_silence_ms,
                self.maximum_unconfirmed_ms,
                self.maximum_utterance_ms,
                self.short_foreground_voice_ms,
            )
            <= 0
        ):
            raise ValueError("endpoint settings must be positive")
        if self.foreground_rms_threshold <= self.rms_threshold:
            raise ValueError("foreground RMS threshold must exceed the candidate threshold")
        if self.barge_in_rms_threshold < self.foreground_rms_threshold:
            raise ValueError("barge-in RMS threshold must not be below the foreground threshold")
        if self.strong_barge_in_rms_threshold <= self.barge_in_rms_threshold:
            raise ValueError("strong barge-in RMS threshold must exceed the normal threshold")
        if self.strong_barge_in_confirmation_ms < self.barge_in_confirmation_ms:
            raise ValueError("strong barge-in confirmation must not be shorter than normal")
        if self.minimum_foreground_voice_ms > self.maximum_unconfirmed_ms:
            raise ValueError("foreground confirmation must fit inside the unconfirmed timeout")
        if not 0 < self.minimum_maximum_voice_ratio <= 1:
            raise ValueError("maximum-utterance voice ratio must be between zero and one")
        if self.minimum_speech_windows <= 0:
            raise ValueError("minimum speech windows must be positive")
        if not 0 < self.speech_probability < 1:
            raise ValueError("speech probability must be between zero and one")
        if not 0 < self.strong_barge_in_minimum_probability < self.speech_probability:
            raise ValueError("strong barge-in probability must be below the speech threshold")
        if not 0 <= self.maximum_no_speech_probability <= 1:
            raise ValueError("no-speech probability must be between zero and one")
        if self.maximum_compression_ratio <= 0:
            raise ValueError("compression ratio must be positive")
        if not math.isfinite(self.minimum_average_log_probability) or not math.isfinite(
            self.short_minimum_average_log_probability
        ):
            raise ValueError("decoder confidence thresholds must be finite")
        if self.short_minimum_average_log_probability < self.minimum_average_log_probability:
            raise ValueError("short decoder confidence must not be looser than the general floor")

    @classmethod
    def from_environment(cls) -> EndpointSettings:
        return cls(
            rms_threshold=float(os.environ.get("RADCLIFFE_ENDPOINT_RMS", "600")),
            pre_roll_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_PRE_ROLL_MS", "600")),
            foreground_rms_threshold=float(
                os.environ.get("RADCLIFFE_ENDPOINT_FOREGROUND_RMS", "1200")
            ),
            barge_in_rms_threshold=float(os.environ.get("RADCLIFFE_ENDPOINT_BARGE_IN_RMS", "1800")),
            strong_barge_in_rms_threshold=float(
                os.environ.get("RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_RMS", "8000")
            ),
            minimum_voice_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_MIN_VOICE_MS", "250")),
            minimum_foreground_voice_ms=float(
                os.environ.get("RADCLIFFE_ENDPOINT_MIN_FOREGROUND_VOICE_MS", "160")
            ),
            barge_in_confirmation_ms=float(
                os.environ.get("RADCLIFFE_ENDPOINT_BARGE_IN_CONFIRMATION_MS", "120")
            ),
            strong_barge_in_confirmation_ms=float(
                os.environ.get("RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_CONFIRMATION_MS", "400")
            ),
            barge_in_release_ms=float(
                os.environ.get("RADCLIFFE_ENDPOINT_BARGE_IN_RELEASE_MS", "120")
            ),
            end_silence_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_SILENCE_MS", "900")),
            maximum_unconfirmed_ms=float(
                os.environ.get("RADCLIFFE_ENDPOINT_MAX_UNCONFIRMED_MS", "2000")
            ),
            maximum_utterance_ms=float(
                os.environ.get("RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS", "30000")
            ),
            minimum_maximum_voice_ratio=float(
                os.environ.get("RADCLIFFE_ENDPOINT_MAX_MIN_VOICE_RATIO", "0.05")
            ),
            minimum_speech_windows=int(
                os.environ.get("RADCLIFFE_ENDPOINT_MIN_SPEECH_WINDOWS", "2")
            ),
            speech_probability=float(
                os.environ.get("RADCLIFFE_ENDPOINT_SPEECH_PROBABILITY", "0.5")
            ),
            strong_barge_in_minimum_probability=float(
                os.environ.get("RADCLIFFE_ENDPOINT_STRONG_BARGE_IN_MIN_PROBABILITY", "0.15")
            ),
            minimum_average_log_probability=float(
                os.environ.get("RADCLIFFE_WHISPER_MIN_AVG_LOGPROB", "-1.5")
            ),
            short_foreground_voice_ms=float(
                os.environ.get("RADCLIFFE_WHISPER_SHORT_FOREGROUND_MS", "700")
            ),
            short_minimum_average_log_probability=float(
                os.environ.get("RADCLIFFE_WHISPER_SHORT_MIN_AVG_LOGPROB", "-0.9")
            ),
            maximum_no_speech_probability=float(
                os.environ.get("RADCLIFFE_WHISPER_MAX_NO_SPEECH_PROBABILITY", "0.6")
            ),
            maximum_compression_ratio=float(
                os.environ.get("RADCLIFFE_WHISPER_MAX_COMPRESSION_RATIO", "2.4")
            ),
        )


class EndpointProcessor(FrameProcessor):
    def __init__(
        self,
        speech: SpeechProvider,
        settings: EndpointSettings | None = None,
        activity: SpeechActivityAnalyzer | None = None,
        capture: SpeechCapture | None = None,
        session_actions: SessionActionClient | None = None,
    ) -> None:
        super().__init__(name="radcliffe-endpoint")
        self.speech = speech
        self.settings = settings or EndpointSettings()
        self.activity = activity
        self.capture = capture
        self.session_actions = session_actions
        self.input_ready_handler: Callable[[str], Awaitable[None]] | None = None
        self.subscribed_participants: set[str] = set()
        self.input_ready_participants: set[str] = set()
        self.voice: VoicePipeline | None = None
        self.buffer = bytearray()
        self.idle_buffer = bytearray()
        self.speaking = False
        self.silence_ms = 0.0
        self.voice_ms = 0.0
        self.foreground_voice_ms = 0.0
        self.barge_in_voice_ms = 0.0
        self.barge_in_silence_ms = 0.0
        self.barge_in_analyzed_voice_ms = 0.0
        self.maximum_rms = 0.0
        self.rms_total = 0.0
        self.rms_frames = 0
        self.started_during_active_turn = False
        self.barge_in_confirmed = False
        self.candidate_pre_roll_ms = 0.0
        self.candidate_participant_id: str | None = None
        self.turn_task: asyncio.Task[None] | None = None
        self.paused = False

    def foreground_quiet(self) -> bool:
        return (
            not self.paused
            and not self.speaking
            and not self.buffer
            and (self.turn_task is None or self.turn_task.done())
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, UserAudioRawFrame):
            await self.push_frame(frame, direction)
            return
        if self.paused:
            return
        participant_id = frame.user_id
        if (
            participant_id
            and participant_id in self.subscribed_participants
            and participant_id not in self.input_ready_participants
            and self.input_ready_handler is not None
        ):
            await self.input_ready_handler(participant_id)
            self.input_ready_participants.add(participant_id)
        if self.capture is not None:
            self.capture.observe_audio_frame(participant_id)
        duration_ms = len(frame.audio) / (2 * frame.num_channels * frame.sample_rate) * 1000
        samples = array("h")
        samples.frombytes(frame.audio)
        rms = math.sqrt(sum(sample * sample for sample in samples) / max(len(samples), 1))
        voiced = rms >= self.settings.rms_threshold
        foreground_voiced = rms >= self.settings.foreground_rms_threshold
        barge_in_voiced = rms >= self.settings.barge_in_rms_threshold
        if voiced and not self.speaking:
            pre_roll = bytes(self.idle_buffer)
            self.idle_buffer.clear()
            self.speaking = True
            self._reset_candidate(preserve_speaking=True)
            self.buffer.extend(pre_roll)
            self.candidate_pre_roll_ms = (
                len(pre_roll) / (2 * frame.num_channels * frame.sample_rate) * 1000
            )
            self.candidate_participant_id = frame.user_id
            if self.capture is not None:
                self.capture.candidate_started(frame.user_id)
            voice_active = self.voice is not None and self.voice.foreground_turn_active()
            self.started_during_active_turn = voice_active or (
                self.turn_task is not None and not self.turn_task.done()
            )
        if not self.speaking:
            self._remember_idle_audio(frame.audio, frame.sample_rate, frame.num_channels)
            return
        self.buffer.extend(frame.audio)
        self.voice_ms += duration_ms if voiced else 0
        self.foreground_voice_ms += duration_ms if foreground_voiced else 0
        self.maximum_rms = max(self.maximum_rms, rms)
        self.rms_total += rms
        self.rms_frames += 1
        foreground_confirmed = self.foreground_voice_ms >= self.settings.minimum_foreground_voice_ms
        endpoint_voiced = foreground_voiced if foreground_confirmed else voiced
        self.silence_ms = 0 if endpoint_voiced else self.silence_ms + duration_ms
        if barge_in_voiced:
            self.barge_in_voice_ms += duration_ms
            self.barge_in_silence_ms = 0
        else:
            self.barge_in_silence_ms += duration_ms
            if self.barge_in_silence_ms >= self.settings.barge_in_release_ms:
                self.barge_in_voice_ms = 0
                self.barge_in_analyzed_voice_ms = 0
        if self.started_during_active_turn and not self.barge_in_confirmed:
            await self._maybe_confirm_barge_in(frame.sample_rate, frame.num_channels)
        utterance_ms = len(self.buffer) / (2 * frame.num_channels * frame.sample_rate) * 1000
        if utterance_ms >= self.settings.maximum_unconfirmed_ms and not foreground_confirmed:
            logger.info(
                "voice endpoint rejected reason=unconfirmed_background audio_ms={} voice_ms={} "
                "foreground_voice_ms={} maximum_rms={} average_rms={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
                round(self.foreground_voice_ms, 2),
                round(self.maximum_rms, 2),
                round(self.rms_total / max(self.rms_frames, 1), 2),
            )
            self._reset_candidate()
            return
        silence_endpoint = self.silence_ms >= self.settings.end_silence_ms
        maximum_endpoint = utterance_ms >= self.settings.maximum_utterance_ms
        voice_ratio = self.foreground_voice_ms / utterance_ms if utterance_ms else 0.0
        if maximum_endpoint and voice_ratio < self.settings.minimum_maximum_voice_ratio:
            logger.info(
                "voice endpoint rejected reason=low_maximum_voice_occupancy audio_ms={} "
                "voice_ms={} foreground_voice_ms={} voice_ratio={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
                round(self.foreground_voice_ms, 2),
                round(voice_ratio, 4),
            )
            self._reset_candidate()
            return
        if (
            silence_endpoint or maximum_endpoint
        ) and self.foreground_voice_ms < self.settings.minimum_foreground_voice_ms:
            logger.info(
                "voice endpoint rejected reason=insufficient_foreground_speech audio_ms={} "
                "voice_ms={} foreground_voice_ms={} maximum_rms={} average_rms={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
                round(self.foreground_voice_ms, 2),
                round(self.maximum_rms, 2),
                round(self.rms_total / max(self.rms_frames, 1), 2),
            )
            self._reset_candidate()
            return
        if (
            (silence_endpoint or maximum_endpoint)
            and self.started_during_active_turn
            and not self.barge_in_confirmed
        ):
            logger.info(
                "voice endpoint rejected reason=unconfirmed_barge_in audio_ms={} voice_ms={} "
                "foreground_voice_ms={} barge_in_voice_ms={} maximum_rms={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
                round(self.foreground_voice_ms, 2),
                round(self.barge_in_voice_ms, 2),
                round(self.maximum_rms, 2),
            )
            self._reset_candidate()
            return
        if (
            silence_endpoint or maximum_endpoint
        ) and self.voice_ms >= self.settings.minimum_voice_ms:
            audio = bytes(self.buffer)
            pre_roll_ms = self.candidate_pre_roll_ms
            candidate_participant_id = self.candidate_participant_id
            voice_ms = self.voice_ms
            foreground_voice_ms = self.foreground_voice_ms
            maximum_rms = self.maximum_rms
            average_rms = self.rms_total / max(self.rms_frames, 1)
            trailing_silence_ms = self.silence_ms
            logger.info(
                "voice turn phase complete phase=endpoint reason={} audio_ms={} voice_ms={} "
                "foreground_voice_ms={} trailing_silence_ms={} maximum_rms={} average_rms={}",
                "maximum_utterance" if maximum_endpoint else "silence",
                round(utterance_ms, 2),
                round(voice_ms, 2),
                round(foreground_voice_ms, 2),
                round(self.silence_ms, 2),
                round(maximum_rms, 2),
                round(average_rms, 2),
            )
            self._reset_candidate()
            self.turn_task = asyncio.create_task(
                self._complete(
                    audio,
                    frame.sample_rate,
                    frame.num_channels,
                    voice_ms=voice_ms,
                    foreground_voice_ms=foreground_voice_ms,
                    voice_ratio=voice_ratio,
                    endpoint_reason="maximum_utterance" if maximum_endpoint else "silence",
                    pre_roll_ms=max(0.0, pre_roll_ms),
                    trailing_silence_ms=trailing_silence_ms,
                    participant_id=candidate_participant_id,
                ),
                name="radcliffe-voice-turn",
            )
        elif silence_endpoint or maximum_endpoint:
            logger.info(
                "voice endpoint rejected reason=insufficient_rms_speech audio_ms={} voice_ms={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
            )
            self._reset_candidate()

    async def _maybe_confirm_barge_in(self, sample_rate: int, channels: int) -> None:
        if (
            self.barge_in_voice_ms - self.barge_in_analyzed_voice_ms
            < self.settings.barge_in_confirmation_ms
        ):
            return
        self.barge_in_analyzed_voice_ms = self.barge_in_voice_ms
        speech_windows: int | None = None
        maximum_speech_probability: float | None = None
        verifier = "speech_activity"
        if self.activity is not None:
            evidence = await self.activity.analyze(bytes(self.buffer), sample_rate, channels)
            speech_windows = evidence.speech_window_count
            maximum_speech_probability = evidence.maximum_probability
            if speech_windows < self.settings.minimum_speech_windows:
                strong_zero_window_candidate = (
                    speech_windows == 0
                    and self.barge_in_voice_ms >= self.settings.strong_barge_in_confirmation_ms
                    and self.maximum_rms >= self.settings.strong_barge_in_rms_threshold
                    and maximum_speech_probability
                    >= self.settings.strong_barge_in_minimum_probability
                )
                if strong_zero_window_candidate:
                    verifier = "high_energy_fallback"
                else:
                    disposition = (
                        "pending_high_energy_fallback"
                        if speech_windows == 0
                        and self.maximum_rms >= self.settings.strong_barge_in_rms_threshold
                        and maximum_speech_probability
                        >= self.settings.strong_barge_in_minimum_probability
                        else "rejected"
                    )
                    logger.info(
                        "voice barge-in candidate disposition={} "
                        "reason=insufficient_speech_activity barge_in_voice_ms={} "
                        "foreground_voice_ms={} maximum_rms={} speech_windows={} "
                        "maximum_speech_probability={}",
                        disposition,
                        round(self.barge_in_voice_ms, 2),
                        round(self.foreground_voice_ms, 2),
                        round(self.maximum_rms, 2),
                        speech_windows,
                        round(maximum_speech_probability, 4),
                    )
                    return
        self.barge_in_confirmed = True
        logger.info(
            "voice barge-in confirmed verifier={} barge_in_voice_ms={} "
            "foreground_voice_ms={} maximum_rms={} speech_windows={} "
            "maximum_speech_probability={}",
            verifier,
            round(self.barge_in_voice_ms, 2),
            round(self.foreground_voice_ms, 2),
            round(self.maximum_rms, 2),
            speech_windows,
            round(maximum_speech_probability, 4)
            if maximum_speech_probability is not None
            else None,
        )
        await self._cancel_current_turn(
            preserve_candidate=True,
            verifier=verifier,
            evidence_duration_ms=self.barge_in_voice_ms,
        )

    def _reset_candidate(self, *, preserve_speaking: bool = False) -> None:
        self.speaking = preserve_speaking
        self.buffer.clear()
        self.silence_ms = 0
        self.voice_ms = 0
        self.foreground_voice_ms = 0
        self.barge_in_voice_ms = 0
        self.barge_in_silence_ms = 0
        self.barge_in_analyzed_voice_ms = 0
        self.maximum_rms = 0
        self.rms_total = 0
        self.rms_frames = 0
        self.started_during_active_turn = False
        self.barge_in_confirmed = False
        self.candidate_pre_roll_ms = 0
        self.candidate_participant_id = None

    def _remember_idle_audio(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        maximum_bytes = round(self.settings.pre_roll_ms / 1000 * sample_rate * channels * 2)
        self.idle_buffer.extend(pcm)
        if len(self.idle_buffer) > maximum_bytes:
            del self.idle_buffer[: len(self.idle_buffer) - maximum_bytes]

    async def _complete(
        self,
        pcm: bytes,
        sample_rate: int,
        channels: int,
        *,
        voice_ms: float,
        foreground_voice_ms: float,
        voice_ratio: float,
        endpoint_reason: str,
        pre_roll_ms: float,
        trailing_silence_ms: float,
        participant_id: str | None,
    ) -> None:
        started_at = monotonic()
        try:
            speech_windows: int | None = None
            maximum_speech_probability: float | None = None
            if self.activity is not None:
                activity = await self.activity.analyze(pcm, sample_rate, channels)
                speech_windows = activity.speech_window_count
                maximum_speech_probability = activity.maximum_probability
                if activity.speech_window_count < self.settings.minimum_speech_windows:
                    logger.info(
                        "voice endpoint rejected reason=insufficient_speech_activity "
                        "audio_ms={} voice_ms={} voice_ratio={} speech_windows={} "
                        "activity_windows={} maximum_speech_probability={}",
                        round(len(pcm) / (2 * channels * sample_rate) * 1000, 2),
                        round(voice_ms, 2),
                        round(voice_ratio, 4),
                        activity.speech_window_count,
                        activity.window_count,
                        round(activity.maximum_probability, 4),
                    )
                    return
            if isinstance(self.speech, DetailedTranscriber):
                transcription = await self.speech.transcribe_with_evidence(
                    pcm, sample_rate, channels
                )
            else:
                transcription = Transcription(
                    await self.speech.transcribe(pcm, sample_rate, channels)
                )
            short_candidate = foreground_voice_ms <= self.settings.short_foreground_voice_ms
            decoder_confidence_floor = (
                self.settings.short_minimum_average_log_probability
                if short_candidate
                else self.settings.minimum_average_log_probability
            )
            assessment = assess_transcription(
                transcription,
                minimum_average_log_probability=decoder_confidence_floor,
                maximum_no_speech_probability=(self.settings.maximum_no_speech_probability),
                maximum_compression_ratio=self.settings.maximum_compression_ratio,
            )
            if self.capture is not None and self.capture.should_capture(participant_id):
                self.capture.record(
                    participant_id,
                    pcm,
                    sample_rate,
                    channels,
                    transcription,
                    assessment,
                    EndpointCaptureEvidence(
                        endpoint_reason=endpoint_reason,
                        audio_ms=len(pcm) / (2 * channels * sample_rate) * 1000,
                        voice_ms=voice_ms,
                        foreground_voice_ms=foreground_voice_ms,
                        voice_ratio=voice_ratio,
                        pre_roll_ms=pre_roll_ms,
                        trailing_silence_ms=trailing_silence_ms,
                    ),
                )
            logger.info(
                "voice turn phase complete phase=transcription duration_ms={} audio_ms={} "
                "accepted={} reason={} voice_ms={} voice_ratio={} speech_windows={} "
                "maximum_speech_probability={} segment_count={} average_log_probability={} "
                "maximum_no_speech_probability={} maximum_compression_ratio={} "
                "foreground_voice_ms={} decoder_confidence_floor={} short_candidate={}",
                round((monotonic() - started_at) * 1000, 2),
                round(len(pcm) / (2 * channels * sample_rate) * 1000, 2),
                assessment.accepted,
                assessment.reason,
                round(voice_ms, 2),
                round(voice_ratio, 4),
                speech_windows,
                round(maximum_speech_probability, 4)
                if maximum_speech_probability is not None
                else None,
                assessment.segment_count,
                round(assessment.average_log_probability, 4)
                if assessment.average_log_probability is not None
                else None,
                round(assessment.maximum_no_speech_probability, 4)
                if assessment.maximum_no_speech_probability is not None
                else None,
                round(assessment.maximum_compression_ratio, 4)
                if assessment.maximum_compression_ratio is not None
                else None,
                round(foreground_voice_ms, 2),
                decoder_confidence_floor,
                short_candidate,
            )
            if self.voice is not None and not self.paused:
                if assessment.accepted:
                    await self.voice.handle_transcript(transcription.text)
                elif assessment.reason == "decoder_disagreement" or (
                    assessment.reason == "low_log_probability" and not short_candidate
                ):
                    await self.voice.request_clarification(assessment.reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("voice turn failed before completion type={}", type(error).__name__)
        finally:
            if self.turn_task is asyncio.current_task():
                self.turn_task = None

    async def interrupt(self) -> None:
        self.paused = False
        await self._cancel_current_turn()

    async def _cancel_current_turn(
        self,
        *,
        preserve_candidate: bool = False,
        verifier: str = "explicit_control",
        evidence_duration_ms: float | None = None,
    ) -> None:
        turn_task = self.turn_task
        if not preserve_candidate:
            self._reset_candidate()
        if self.voice is not None:
            await self.voice.barge_in(
                verifier=verifier,
                evidence_duration_ms=evidence_duration_ms,
            )
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
        if self.turn_task is turn_task:
            self.turn_task = None

    async def pause(self) -> None:
        self.paused = True
        self._reset_candidate()
        self.idle_buffer.clear()
        if self.voice is not None:
            await self.voice.pause()
        turn_task = self.turn_task
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
        if self.turn_task is turn_task:
            self.turn_task = None

    async def resume(self) -> None:
        self.paused = False
        if self.voice is not None:
            await self.voice.resume()

    async def participant_connected(self) -> None:
        self.paused = False
        self._reset_candidate()
        self.idle_buffer.clear()
        if self.voice is not None:
            await self.voice.participant_connected()

    def audio_track_subscribed(self, participant_id: str) -> None:
        self.subscribed_participants.add(participant_id)
        self.input_ready_participants.discard(participant_id)

    async def stop(self) -> None:
        self.paused = True
        await self._cancel_current_turn()
        if self.voice is not None:
            await self.voice.stop()

    async def playback_acknowledgement(self, acknowledgement: PlaybackAcknowledgement) -> None:
        if self.voice is None:
            raise RuntimeError("voice pipeline is not ready")
        await self.voice.playback_acknowledgement(acknowledgement)

    async def agent_control_confirmation(
        self,
        confirmation: protocol_pb2.AgentControlConfirmation,
        participant_identity: str,
    ) -> None:
        if self.voice is None or self.session_actions is None:
            raise RuntimeError("session action confirmation is unavailable")
        result = await self.session_actions.confirm(
            confirmation,
            participant_identity=participant_identity,
        )
        await self.voice.complete_session_action(result)

    @staticmethod
    def duration_ms(pcm_audio: bytes) -> int:
        return int(len(pcm_audio) / (2 * 24000) * 1000)

    async def publish(
        self,
        _segment_id: str,
        pcm_audio: bytes,
        *,
        wait_for_playback: bool = False,
    ) -> int:
        await self.push_frame(OutputAudioRawFrame(pcm_audio, 24000, 1))
        duration_ms = self.duration_ms(pcm_audio)
        if wait_for_playback:
            await asyncio.sleep(duration_ms / 1000)
        return duration_ms

    async def flush(self) -> None:
        await self.push_frame(InterruptionFrame())


async def run() -> None:
    required = {
        key: os.environ.get(key)
        for key in (
            "RADCLIFFE_LIVEKIT_URL",
            "RADCLIFFE_LIVEKIT_TOKEN",
            "RADCLIFFE_SESSION_ID",
        )
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required settings: {', '.join(missing)}")
    database_path = Path(os.environ.get("RADCLIFFE_DATABASE_PATH", "var/radcliffe.sqlite3"))
    store = EventStore(database_path)
    await store.migrate()
    speech_provider = os.environ.get("RADCLIFFE_SPEECH_PROVIDER", "local")
    if speech_provider == "local":
        speech: SpeechProvider = LocalAppleSpeechProvider(
            whisper_model=os.environ.get(
                "RADCLIFFE_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"
            ),
            language=os.environ.get("RADCLIFFE_WHISPER_LANGUAGE", "en"),
            voice=os.environ.get("RADCLIFFE_TTS_VOICE", "Samantha"),
            initial_prompt=os.environ.get(
                "RADCLIFFE_WHISPER_INITIAL_PROMPT",
                DEFAULT_WHISPER_INITIAL_PROMPT,
            ),
            verification_model=os.environ.get("RADCLIFFE_WHISPER_VERIFICATION_MODEL"),
        )
    elif speech_provider == "openai":
        if os.environ.get("RADCLIFFE_ALLOW_CLOUD_MODELS") != "1":
            raise RuntimeError(
                "OpenAI speech is disabled; set RADCLIFFE_ALLOW_CLOUD_MODELS=1 "
                "only for a user-approved test run"
            )
        api_key = os.environ.get("RADCLIFFE_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("RADCLIFFE_OPENAI_API_KEY is required for OpenAI speech")
        speech = OpenAISpeechProvider(api_key)
    else:
        raise RuntimeError(f"unsupported speech provider: {speech_provider}")
    endpoint_settings = EndpointSettings.from_environment()
    capture = SpeechCapture.from_environment()
    sidecar_url = os.environ.get("RADCLIFFE_SIDECAR_URL", "http://127.0.0.1:8787")
    endpoint = EndpointProcessor(
        speech,
        endpoint_settings,
        SileroSpeechActivityAnalyzer(speech_probability=endpoint_settings.speech_probability),
        capture,
        SessionActionClient(sidecar_url),
    )
    transport = create_transport(
        LiveKitConnection(
            required["RADCLIFFE_LIVEKIT_URL"] or "",
            required["RADCLIFFE_LIVEKIT_TOKEN"] or "",
            required["RADCLIFFE_SESSION_ID"] or "",
        )
    )
    event_sink = LiveKitEventSink(transport, required["RADCLIFFE_SESSION_ID"] or "")
    voice = VoicePipeline(
        required["RADCLIFFE_SESSION_ID"] or "",
        store,
        SidecarClient(sidecar_url),
        speech,
        endpoint,
        event_sink,
        TelemetrySink.from_environment(),
        model_chunk_chars=int(os.environ.get("RADCLIFFE_MODEL_CHUNK_CHARS", "240")),
        client_playback_acknowledgements=True,
        playback_ack_timeout_seconds=float(
            os.environ.get("RADCLIFFE_PLAYBACK_ACK_TIMEOUT_SECONDS", "2")
        ),
        defer_readiness_until_input=True,
        configuration_registry=ConfigurationRegistry(database_path),
        configuration_owner_id=os.environ.get("RADCLIFFE_OWNER_ID", "local-owner"),
    )
    endpoint.voice = voice
    await voice.start()
    notifications = JobNotificationScheduler(
        store=JobStore(database_path),
        session_id=required["RADCLIFFE_SESSION_ID"] or "",
        events=event_sink,
        voice=voice,
        foreground_quiet=endpoint.foreground_quiet,
        poll_interval_seconds=float(
            os.environ.get("RADCLIFFE_JOB_NOTIFICATION_POLL_SECONDS", "0.25")
        ),
    )
    await notifications.start()
    controls = ClientControlHandler(endpoint)

    async def on_data_received(
        active_transport: RadcliffeLiveKitTransport, data: bytes, participant_id: str
    ) -> None:
        if not await controls.handle(
            data,
            active_transport.participant_identity(participant_id),
        ):
            logger.warning("ignored unsupported Protocol v2 client event")

    async def on_participant_connected(
        active_transport: RadcliffeLiveKitTransport, participant_id: str
    ) -> None:
        if capture is not None:
            capture.authorize_participant(
                participant_id,
                active_transport.participant_identity(participant_id),
            )
        await endpoint.participant_connected()
        await notifications.replay_visibility(participant_id)

    async def on_audio_track_subscribed(
        active_transport: RadcliffeLiveKitTransport, participant_id: str
    ) -> None:
        if capture is not None:
            capture.authorize_participant(
                participant_id,
                active_transport.participant_identity(participant_id),
            )
            capture.track_subscribed(participant_id)
        endpoint.audio_track_subscribed(participant_id)

    async def on_input_ready(participant_id: str) -> None:
        generation = await voice.input_ready()
        await event_sink.session_ready(generation, participant_id)
        notifications.wake()
        logger.info(
            "voice input ready participant_id={} generation={}",
            participant_id,
            generation,
        )

    endpoint.input_ready_handler = on_input_ready

    transport.add_event_handler("on_data_received", on_data_received)
    transport.add_event_handler("on_participant_connected", on_participant_connected)
    transport.add_event_handler("on_audio_track_subscribed", on_audio_track_subscribed)
    pipeline = Pipeline([transport.input(), endpoint, transport.output()])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
        ),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    runner = WorkerRunner(handle_sigint=True)
    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        await notifications.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
