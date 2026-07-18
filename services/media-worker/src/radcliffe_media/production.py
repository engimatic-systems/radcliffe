from __future__ import annotations

import asyncio
import math
import os
from array import array
from dataclasses import dataclass
from pathlib import Path

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
from radcliffe_media.speech import LocalAppleSpeechProvider, OpenAISpeechProvider, SpeechProvider


@dataclass(frozen=True, slots=True)
class EndpointSettings:
    rms_threshold: float = 600
    minimum_voice_ms: float = 250
    end_silence_ms: float = 900

    def __post_init__(self) -> None:
        if min(self.rms_threshold, self.minimum_voice_ms, self.end_silence_ms) <= 0:
            raise ValueError("endpoint settings must be positive")

    @classmethod
    def from_environment(cls) -> EndpointSettings:
        return cls(
            rms_threshold=float(os.environ.get("RADCLIFFE_ENDPOINT_RMS", "600")),
            minimum_voice_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_MIN_VOICE_MS", "250")),
            end_silence_ms=float(os.environ.get("RADCLIFFE_ENDPOINT_SILENCE_MS", "900")),
        )


class EndpointProcessor(FrameProcessor):
    def __init__(self, speech: SpeechProvider, settings: EndpointSettings | None = None) -> None:
        super().__init__(name="radcliffe-endpoint")
        self.speech = speech
        self.settings = settings or EndpointSettings()
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
        if (
            self.silence_ms >= self.settings.end_silence_ms
            and self.voice_ms >= self.settings.minimum_voice_ms
        ):
            audio = bytes(self.buffer)
            self.speaking = False
            self.buffer.clear()
            self.turn_task = asyncio.create_task(
                self._complete(audio, frame.sample_rate, frame.num_channels),
                name="radcliffe-voice-turn",
            )

    async def _complete(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        try:
            transcript = await self.speech.transcribe(pcm, sample_rate, channels)
            if transcript and self.voice is not None and not self.paused:
                await self.voice.handle_transcript(transcript)
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
        await self._cancel_current_turn()
        if self.voice is not None:
            await self.voice.pause()

    async def resume(self) -> None:
        self.paused = False
        if self.voice is not None:
            await self.voice.resume()

    async def stop(self) -> None:
        self.paused = True
        await self._cancel_current_turn()
        if self.voice is not None:
            await self.voice.stop()

    async def publish(self, _segment_id: str, pcm_audio: bytes) -> int:
        await self.push_frame(OutputAudioRawFrame(pcm_audio, 24000, 1))
        return int(len(pcm_audio) / (2 * 24000) * 1000)

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
    endpoint = EndpointProcessor(speech, EndpointSettings.from_environment())
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
