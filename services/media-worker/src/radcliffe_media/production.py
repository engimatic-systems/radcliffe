from __future__ import annotations

import asyncio
import math
import os
from array import array
from pathlib import Path

from pipecat.frames.frames import Frame, InterruptionFrame, OutputAudioRawFrame, UserAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from radcliffe_domain.store import EventStore

from radcliffe_media.livekit_adapter import LiveKitConnection, create_transport
from radcliffe_media.pipeline import VoicePipeline
from radcliffe_media.sidecar import SidecarClient
from radcliffe_media.speech import OpenAISpeechProvider


class EndpointProcessor(FrameProcessor):
    def __init__(self, speech: OpenAISpeechProvider) -> None:
        super().__init__(name="radcliffe-endpoint")
        self.speech = speech
        self.voice: VoicePipeline | None = None
        self.buffer = bytearray()
        self.speaking = False
        self.silence_ms = 0.0
        self.voice_ms = 0.0
        self.turn_task: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, UserAudioRawFrame):
            await self.push_frame(frame, direction)
            return
        duration_ms = len(frame.audio) / (2 * frame.num_channels * frame.sample_rate) * 1000
        samples = array("h")
        samples.frombytes(frame.audio)
        rms = math.sqrt(sum(sample * sample for sample in samples) / max(len(samples), 1))
        voiced = rms >= 600
        if voiced and not self.speaking:
            self.speaking = True
            self.buffer.clear()
            self.voice_ms = 0
            self.silence_ms = 0
            if self.turn_task is not None and not self.turn_task.done() and self.voice is not None:
                await self.voice.barge_in()
        if not self.speaking:
            return
        self.buffer.extend(frame.audio)
        self.voice_ms += duration_ms if voiced else 0
        self.silence_ms = 0 if voiced else self.silence_ms + duration_ms
        if self.silence_ms >= 600 and self.voice_ms >= 250:
            audio = bytes(self.buffer)
            self.speaking = False
            self.buffer.clear()
            self.turn_task = asyncio.create_task(
                self._complete(audio, frame.sample_rate, frame.num_channels),
                name="radcliffe-voice-turn",
            )

    async def _complete(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        transcript = await self.speech.transcribe(pcm, sample_rate, channels)
        if transcript and self.voice is not None:
            await self.voice.handle_transcript(transcript)

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
            "RADCLIFFE_OPENAI_API_KEY",
        )
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required settings: {', '.join(missing)}")
    store = EventStore(Path(os.environ.get("RADCLIFFE_DATABASE_PATH", "var/radcliffe.sqlite3")))
    await store.migrate()
    speech = OpenAISpeechProvider(required["RADCLIFFE_OPENAI_API_KEY"] or "")
    endpoint = EndpointProcessor(speech)
    voice = VoicePipeline(
        required["RADCLIFFE_SESSION_ID"] or "",
        store,
        SidecarClient(os.environ.get("RADCLIFFE_SIDECAR_URL", "http://127.0.0.1:8787")),
        speech,
        endpoint,
    )
    endpoint.voice = voice
    await voice.start()
    transport = create_transport(
        LiveKitConnection(
            required["RADCLIFFE_LIVEKIT_URL"] or "",
            required["RADCLIFFE_LIVEKIT_TOKEN"] or "",
            required["RADCLIFFE_SESSION_ID"] or "",
        )
    )
    pipeline = Pipeline([transport.input(), endpoint, transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
        ),
    )
    await PipelineRunner(handle_sigint=True).run(task)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
