from __future__ import annotations

import asyncio
import math
import os
from array import array
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame, OutputAudioRawFrame, UserAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner
from radcliffe_domain.store import EventStore

from radcliffe_media.control import ClientControlHandler
from radcliffe_media.events import LiveKitEventSink
from radcliffe_media.livekit_adapter import (
    LiveKitConnection,
    RadcliffeLiveKitTransport,
    create_transport,
)
from radcliffe_media.pipeline import VoicePipeline
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


@dataclass(frozen=True, slots=True)
class EndpointSettings:
    rms_threshold: float = 600
    minimum_voice_ms: float = 250
    end_silence_ms: float = 900
    maximum_utterance_ms: float = 30000
    minimum_maximum_voice_ratio: float = 0.05
    minimum_speech_windows: int = 2
    speech_probability: float = 0.5
    minimum_average_log_probability: float = -1.5
    maximum_no_speech_probability: float = 0.6
    maximum_compression_ratio: float = 2.4

    def __post_init__(self) -> None:
        if (
            min(
                self.rms_threshold,
                self.minimum_voice_ms,
                self.end_silence_ms,
                self.maximum_utterance_ms,
            )
            <= 0
        ):
            raise ValueError("endpoint settings must be positive")
        if not 0 < self.minimum_maximum_voice_ratio <= 1:
            raise ValueError("maximum-utterance voice ratio must be between zero and one")
        if self.minimum_speech_windows <= 0:
            raise ValueError("minimum speech windows must be positive")
        if not 0 < self.speech_probability < 1:
            raise ValueError("speech probability must be between zero and one")
        if not 0 <= self.maximum_no_speech_probability <= 1:
            raise ValueError("no-speech probability must be between zero and one")
        if self.maximum_compression_ratio <= 0:
            raise ValueError("compression ratio must be positive")

    @classmethod
    def from_environment(cls) -> EndpointSettings:
        return cls(
            rms_threshold=float(os.environ.get("RADCLIFFE_ENDPOINT_RMS", "600")),
            minimum_voice_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_MIN_VOICE_MS", "250")),
            end_silence_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_SILENCE_MS", "900")),
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
            minimum_average_log_probability=float(
                os.environ.get("RADCLIFFE_WHISPER_MIN_AVG_LOGPROB", "-1.5")
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
    ) -> None:
        super().__init__(name="radcliffe-endpoint")
        self.speech = speech
        self.settings = settings or EndpointSettings()
        self.activity = activity
        self.voice: VoicePipeline | None = None
        self.buffer = bytearray()
        self.speaking = False
        self.silence_ms = 0.0
        self.voice_ms = 0.0
        self.turn_task: asyncio.Task[None] | None = None
        self.paused = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, UserAudioRawFrame):
            await self.push_frame(frame, direction)
            return
        if self.paused:
            return
        duration_ms = len(frame.audio) / (2 * frame.num_channels * frame.sample_rate) * 1000
        samples = array("h")
        samples.frombytes(frame.audio)
        rms = math.sqrt(sum(sample * sample for sample in samples) / max(len(samples), 1))
        voiced = rms >= self.settings.rms_threshold
        if voiced and not self.speaking:
            if self.turn_task is not None and not self.turn_task.done():
                await self._cancel_current_turn()
            self.speaking = True
            self.buffer.clear()
            self.voice_ms = 0
            self.silence_ms = 0
        if not self.speaking:
            return
        self.buffer.extend(frame.audio)
        self.voice_ms += duration_ms if voiced else 0
        self.silence_ms = 0 if voiced else self.silence_ms + duration_ms
        utterance_ms = len(self.buffer) / (2 * frame.num_channels * frame.sample_rate) * 1000
        silence_endpoint = self.silence_ms >= self.settings.end_silence_ms
        maximum_endpoint = utterance_ms >= self.settings.maximum_utterance_ms
        voice_ratio = self.voice_ms / utterance_ms if utterance_ms else 0.0
        if maximum_endpoint and voice_ratio < self.settings.minimum_maximum_voice_ratio:
            logger.info(
                "voice endpoint rejected reason=low_maximum_voice_occupancy audio_ms={} "
                "voice_ms={} voice_ratio={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
                round(voice_ratio, 4),
            )
            self.speaking = False
            self.buffer.clear()
            self.voice_ms = 0
            self.silence_ms = 0
            return
        if (
            silence_endpoint or maximum_endpoint
        ) and self.voice_ms >= self.settings.minimum_voice_ms:
            audio = bytes(self.buffer)
            logger.info(
                "voice turn phase complete phase=endpoint reason={} audio_ms={} voice_ms={} "
                "trailing_silence_ms={}",
                "maximum_utterance" if maximum_endpoint else "silence",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
                round(self.silence_ms, 2),
            )
            self.speaking = False
            self.buffer.clear()
            self.turn_task = asyncio.create_task(
                self._complete(
                    audio,
                    frame.sample_rate,
                    frame.num_channels,
                    voice_ms=self.voice_ms,
                    voice_ratio=voice_ratio,
                ),
                name="radcliffe-voice-turn",
            )
        elif silence_endpoint or maximum_endpoint:
            logger.info(
                "voice endpoint rejected reason=insufficient_rms_speech audio_ms={} voice_ms={}",
                round(utterance_ms, 2),
                round(self.voice_ms, 2),
            )
            self.speaking = False
            self.buffer.clear()
            self.voice_ms = 0
            self.silence_ms = 0

    async def _complete(
        self,
        pcm: bytes,
        sample_rate: int,
        channels: int,
        *,
        voice_ms: float,
        voice_ratio: float,
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
            assessment = assess_transcription(
                transcription,
                minimum_average_log_probability=(self.settings.minimum_average_log_probability),
                maximum_no_speech_probability=(self.settings.maximum_no_speech_probability),
                maximum_compression_ratio=self.settings.maximum_compression_ratio,
            )
            logger.info(
                "voice turn phase complete phase=transcription duration_ms={} audio_ms={} "
                "accepted={} reason={} voice_ms={} voice_ratio={} speech_windows={} "
                "maximum_speech_probability={} segment_count={} average_log_probability={} "
                "maximum_no_speech_probability={} maximum_compression_ratio={}",
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
            )
            if assessment.accepted and self.voice is not None and not self.paused:
                await self.voice.handle_transcript(transcription.text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice turn failed before completion")
        finally:
            if self.turn_task is asyncio.current_task():
                self.turn_task = None

    async def interrupt(self) -> None:
        self.paused = False
        await self._cancel_current_turn()

    async def _cancel_current_turn(self) -> None:
        turn_task = self.turn_task
        self.speaking = False
        self.buffer.clear()
        if self.voice is not None:
            await self.voice.barge_in()
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
        if self.turn_task is turn_task:
            self.turn_task = None

    async def pause(self) -> None:
        self.paused = True
        self.speaking = False
        self.buffer.clear()
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

    async def stop(self) -> None:
        self.paused = True
        await self._cancel_current_turn()
        if self.voice is not None:
            await self.voice.stop()

    async def publish(
        self,
        _segment_id: str,
        pcm_audio: bytes,
        *,
        wait_for_playback: bool = False,
    ) -> int:
        await self.push_frame(OutputAudioRawFrame(pcm_audio, 24000, 1))
        duration_ms = int(len(pcm_audio) / (2 * 24000) * 1000)
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
    store = EventStore(Path(os.environ.get("RADCLIFFE_DATABASE_PATH", "var/radcliffe.sqlite3")))
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
    endpoint = EndpointProcessor(
        speech,
        endpoint_settings,
        SileroSpeechActivityAnalyzer(speech_probability=endpoint_settings.speech_probability),
    )
    transport = create_transport(
        LiveKitConnection(
            required["RADCLIFFE_LIVEKIT_URL"] or "",
            required["RADCLIFFE_LIVEKIT_TOKEN"] or "",
            required["RADCLIFFE_SESSION_ID"] or "",
        )
    )
    voice = VoicePipeline(
        required["RADCLIFFE_SESSION_ID"] or "",
        store,
        SidecarClient(os.environ.get("RADCLIFFE_SIDECAR_URL", "http://127.0.0.1:8787")),
        speech,
        endpoint,
        LiveKitEventSink(transport, required["RADCLIFFE_SESSION_ID"] or ""),
        model_chunk_chars=int(os.environ.get("RADCLIFFE_MODEL_CHUNK_CHARS", "240")),
    )
    endpoint.voice = voice
    await voice.start()
    controls = ClientControlHandler(endpoint)

    async def on_data_received(
        _transport: RadcliffeLiveKitTransport, data: bytes, _participant_id: str
    ) -> None:
        if not await controls.handle(data):
            logger.warning("ignored unsupported Protocol v2 client event")

    transport.add_event_handler("on_data_received", on_data_received)
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
    await runner.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
