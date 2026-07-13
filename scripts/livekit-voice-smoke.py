from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import numpy as np
from livekit import rtc
from radcliffe_media.speech import LocalAppleSpeechProvider
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


@dataclass
class SmokeState:
    output: bytearray = field(default_factory=bytearray)
    events: list[protocol_pb2.ServerEvent] = field(default_factory=list)
    capture_tasks: list[asyncio.Task[None]] = field(default_factory=list)


async def wait_until(predicate, limit_seconds: float = 30.0) -> None:
    async with asyncio.timeout(limit_seconds):
        while not predicate():  # noqa: ASYNC110 - predicates combine several RTC events
            await asyncio.sleep(0.05)


async def pcm_16khz_async(provider: LocalAppleSpeechProvider, text: str) -> bytes:
    pcm_24khz = await provider.synthesize(text)
    waveform = provider._waveform(pcm_24khz, 24000, 1)
    speech = np.clip(waveform * 32768, -32768, 32767).astype("<i2").tobytes()
    return speech + b"\x00\x00" * 16000


async def publish_audio(source: rtc.AudioSource, pcm: bytes) -> None:
    for offset in range(0, len(pcm), 320):
        chunk = pcm[offset : offset + 320].ljust(320, b"\x00")
        await source.capture_frame(rtc.AudioFrame(chunk, 16000, 1, 160))


async def send_control(room: rtc.Room, kind: int) -> None:
    packet = protocol_pb2.ClientEvent(
        protocol_version=2,
        control_command=protocol_pb2.ControlCommand(
            command_id=str(uuid4()),
            kind=kind,
            idempotency_key=str(uuid4()),
        ),
    )
    await room.local_participant.publish_data(
        packet.SerializeToString(deterministic=True),
        reliable=True,
        destination_identities=["radcliffe-worker"],
    )


async def run() -> None:
    control_url = os.environ.get("RADCLIFFE_CONTROL_URL", "http://127.0.0.1:8000")
    bootstrap_token = os.environ.get("RADCLIFFE_BOOTSTRAP_TOKEN")
    session_id = os.environ.get("RADCLIFFE_SESSION_ID", "radcliffe-demo")
    if not bootstrap_token:
        raise RuntimeError("RADCLIFFE_BOOTSTRAP_TOKEN is required")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{control_url.rstrip('/')}/v1/sessions",
            headers={"Authorization": f"Bearer {bootstrap_token}"},
            json={
                "session_id": session_id,
                "participant_identity": "synthetic-voice-smoke",
                "role": "client",
            },
        )
        response.raise_for_status()
        credentials = response.json()

    provider = LocalAppleSpeechProvider()
    paused_audio, resumed_audio, interrupted_audio = await asyncio.gather(
        pcm_16khz_async(provider, "This paused speech must not become a message."),
        pcm_16khz_async(provider, "Reply with exactly: voice controls work."),
        pcm_16khz_async(provider, "Tell me a five sentence story about a lighthouse."),
    )
    room = rtc.Room()
    state = SmokeState()

    async def capture(track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        try:
            async for event in stream:
                state.output.extend(bytes(event.frame.data))
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def on_track_subscribed(track, _publication, participant) -> None:
        if participant.identity == "radcliffe-worker" and track.kind == rtc.TrackKind.KIND_AUDIO:
            state.capture_tasks.append(asyncio.create_task(capture(track)))

    @room.on("data_received")
    def on_data_received(packet: rtc.DataPacket) -> None:
        event = protocol_pb2.ServerEvent()
        try:
            event.ParseFromString(packet.data)
        except Exception:
            return
        if event.protocol_version == 2:
            state.events.append(event)

    await room.connect(credentials["livekit_url"], credentials["participant_token"])
    source = rtc.AudioSource(16000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("synthetic-microphone", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.Value("SOURCE_MICROPHONE")
    await room.local_participant.publish_track(track, options)
    await asyncio.sleep(0.5)

    await send_control(room, protocol_pb2.ControlCommand.KIND_PAUSE)
    await wait_until(
        lambda: any(
            event.HasField("state_changed")
            and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_PAUSED
            for event in state.events
        )
    )
    message_count = sum(event.HasField("message") for event in state.events)
    await publish_audio(source, paused_audio)
    await asyncio.sleep(1)
    pause_blocked_turn = sum(event.HasField("message") for event in state.events) == message_count

    state.output.clear()
    await send_control(room, protocol_pb2.ControlCommand.KIND_RESUME)
    await wait_until(
        lambda: any(
            event.HasField("state_changed")
            and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_LISTENING
            and event.state_changed.reason == "resumed"
            for event in state.events
        )
    )
    started = time.monotonic()
    await publish_audio(source, resumed_audio)
    await wait_until(lambda: len(state.output) >= 24000, limit_seconds=90)
    first_audio_seconds = time.monotonic() - started
    await wait_until(
        lambda: any(
            event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
            for event in state.events
        ),
        limit_seconds=30,
    )
    await wait_until(
        lambda: any(
            event.HasField("state_changed")
            and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_LISTENING
            and event.state_changed.reason == "turn complete"
            for event in state.events
        ),
        limit_seconds=30,
    )

    state.output.clear()
    event_index = len(state.events)
    await publish_audio(source, interrupted_audio)
    await wait_until(
        lambda: any(
            event.HasField("state_changed")
            and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_SPEAKING
            for event in state.events[event_index:]
        ),
        limit_seconds=90,
    )
    await wait_until(lambda: len(state.output) >= 24000, limit_seconds=30)
    interrupt_started = time.monotonic()
    await send_control(room, protocol_pb2.ControlCommand.KIND_INTERRUPT)
    await wait_until(
        lambda: any(
            event.HasField("state_changed") and event.state_changed.reason == "interrupted"
            for event in state.events[event_index:]
        )
    )
    interrupt_seconds = time.monotonic() - interrupt_started

    messages = [
        event.message.content.inline_text
        for event in state.events
        if event.HasField("message") and event.message.content.HasField("inline_text")
    ]
    states = [
        protocol_pb2.ConversationState.Name(event.state_changed.current)
        for event in state.events
        if event.HasField("state_changed")
    ]
    report = {
        "pause_blocked_turn": pause_blocked_turn,
        "first_audio_seconds": round(first_audio_seconds, 3),
        "interrupt_ack_seconds": round(interrupt_seconds, 3),
        "output_bytes": len(state.output),
        "messages": messages,
        "states": states,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if not pause_blocked_turn:
        raise RuntimeError("pause did not suppress the synthetic turn")
    if first_audio_seconds >= 5:
        raise RuntimeError("first return audio exceeded five seconds")
    if interrupt_seconds >= 0.5:
        raise RuntimeError("interrupt acknowledgement exceeded 500 ms")

    await room.disconnect()
    for task in state.capture_tasks:
        task.cancel()
    await asyncio.gather(*state.capture_tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
