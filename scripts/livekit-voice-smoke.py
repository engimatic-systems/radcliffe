from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
from google.protobuf.json_format import MessageToDict
from livekit import rtc
from radcliffe_domain.projection import (
    TURN_DISPOSITION_METADATA,
    TURN_EVENT_METADATA,
    TURN_ID_METADATA,
    context_replay,
)
from radcliffe_domain.store import EventStore
from radcliffe_media.speech import LocalAppleSpeechProvider
from radcliffe_media.speech_quality import assess_transcript
from radcliffe_protocol.radcliffe.v2 import protocol_pb2

ROOT = Path(__file__).resolve().parent.parent
INPUT_RATE = 16000
OUTPUT_RATE = 24000
FRAME_SAMPLES = 320


@dataclass(frozen=True, slots=True)
class Utterance:
    text: str
    speech_pcm: bytes
    pcm: bytes


@dataclass(frozen=True, slots=True)
class TimedEvent:
    received_at: float
    event: protocol_pb2.ServerEvent


@dataclass(frozen=True, slots=True)
class TimedAudio:
    received_at: float
    pcm: bytes


@dataclass(slots=True)
class SmokeState:
    started_at: float = field(default_factory=time.monotonic)
    events: list[TimedEvent] = field(default_factory=list)
    input: list[TimedAudio] = field(default_factory=list)
    output: list[TimedAudio] = field(default_factory=list)
    capture_tasks: list[asyncio.Task[None]] = field(default_factory=list)


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str
    measured: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RecordingAudioSource:
    source: rtc.AudioSource
    state: SmokeState

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        self.state.input.append(TimedAudio(time.monotonic(), bytes(frame.data)))
        await self.source.capture_frame(frame)


@dataclass(slots=True)
class PendingPlayback:
    segment: protocol_pb2.PlaybackSegment
    started: bool = False
    position_ms: int = 0
    last_reported_position_ms: int = 0


@dataclass(slots=True)
class ClientPlaybackTracker:
    pending: deque[PendingPlayback] = field(default_factory=deque)
    known_playback_ids: set[str] = field(default_factory=set)
    sent: list[protocol_pb2.PlaybackAcknowledgement] = field(default_factory=list)

    def announce(self, segment: protocol_pb2.PlaybackSegment) -> bool:
        if not segment.playback_id or not segment.segment_id or not segment.duration_ms:
            return False
        if segment.playback_id in self.known_playback_ids:
            return False
        self.known_playback_ids.add(segment.playback_id)
        self.pending.append(PendingPlayback(segment))
        return True

    def audio_frame(
        self,
        pcm: bytes,
        sample_rate: int,
    ) -> list[protocol_pb2.PlaybackAcknowledgement]:
        if not self.pending or sample_rate <= 0:
            return []
        active = self.pending[0]
        if not active.started and not _active(pcm):
            return []
        acknowledgements: list[protocol_pb2.PlaybackAcknowledgement] = []
        if not active.started:
            active.started = True
            acknowledgements.append(
                self._acknowledgement(
                    active,
                    protocol_pb2.PLAYBACK_STATUS_STARTED,
                    0,
                    active.segment.boundary_index,
                )
            )
        duration_ms = max(1, round(len(pcm) / 2 / sample_rate * 1000))
        active.position_ms = min(
            active.position_ms + duration_ms,
            active.segment.duration_ms,
        )
        if active.position_ms >= active.segment.duration_ms:
            acknowledgements.append(
                self._acknowledgement(
                    active,
                    protocol_pb2.PLAYBACK_STATUS_COMPLETED,
                    active.segment.duration_ms,
                    active.segment.boundary_index + 1,
                )
            )
            self.pending.popleft()
        elif active.position_ms - active.last_reported_position_ms >= 250:
            active.last_reported_position_ms = active.position_ms
            acknowledgements.append(
                self._acknowledgement(
                    active,
                    protocol_pb2.PLAYBACK_STATUS_POSITION,
                    active.position_ms,
                    active.segment.boundary_index,
                )
            )
        self.sent.extend(acknowledgements)
        return acknowledgements

    def flush(self, detail: str) -> list[protocol_pb2.PlaybackAcknowledgement]:
        acknowledgements: list[protocol_pb2.PlaybackAcknowledgement] = []
        while self.pending:
            active = self.pending.popleft()
            acknowledgements.append(
                self._acknowledgement(
                    active,
                    protocol_pb2.PLAYBACK_STATUS_FLUSHED,
                    active.position_ms,
                    active.segment.boundary_index,
                    detail,
                )
            )
        self.sent.extend(acknowledgements)
        return acknowledgements

    @staticmethod
    def _acknowledgement(
        active: PendingPlayback,
        status: int,
        position_ms: int,
        boundary_index: int,
        detail: str = "",
    ) -> protocol_pb2.PlaybackAcknowledgement:
        acknowledgement = protocol_pb2.PlaybackAcknowledgement(
            acknowledgement_id=str(uuid4()),
            playback_id=active.segment.playback_id,
            operation=active.segment.operation,
            segment_id=active.segment.segment_id,
            status=status,
            position_ms=position_ms,
            boundary_index=boundary_index,
            detail=detail,
        )
        acknowledgement.occurred_at.GetCurrentTime()
        return acknowledgement


async def wait_until(predicate, limit_seconds: float = 30.0) -> None:
    async with asyncio.timeout(limit_seconds):
        while not predicate():  # noqa: ASYNC110 - predicates combine several RTC events
            await asyncio.sleep(0.05)


def _event_after(state: SmokeState, index: int, predicate) -> TimedEvent | None:
    return next((item for item in state.events[index:] if predicate(item.event)), None)


def _active(pcm: bytes, threshold: float = 300) -> bool:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if not samples.size:
        return False
    return math.sqrt(float(np.mean(samples**2))) >= threshold


def _contains_emoji(text: str) -> bool:
    return any(
        0x1F000 <= ord(character) <= 0x1FAFF
        or 0x2600 <= ord(character) <= 0x27BF
        or ord(character) == 0xFE0F
        for character in text
    )


def _first_audio_after(state: SmokeState, timestamp: float) -> float | None:
    return next(
        (
            item.received_at
            for item in state.output
            if item.received_at >= timestamp and _active(item.pcm)
        ),
        None,
    )


async def wait_for_audio_after(
    state: SmokeState, timestamp: float, limit_seconds: float = 30
) -> float:
    await wait_until(lambda: _first_audio_after(state, timestamp) is not None, limit_seconds)
    onset = _first_audio_after(state, timestamp)
    assert onset is not None
    return onset


async def wait_for_output_silence(
    state: SmokeState,
    *,
    after: float,
    silence_seconds: float = 0.8,
    limit_seconds: float = 30,
) -> None:
    cursor = 0
    last_active = max(after, time.monotonic())
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        for item in state.output[cursor:]:
            if item.received_at >= after and _active(item.pcm):
                last_active = item.received_at
        cursor = len(state.output)
        if time.monotonic() - last_active >= silence_seconds:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("agent output never became silent")


async def synthesize_utterance(voice: str, text: str) -> Utterance:
    provider = LocalAppleSpeechProvider(voice=voice)
    pcm_24khz = await provider.synthesize(text)
    waveform = provider._waveform(pcm_24khz, OUTPUT_RATE, 1)
    speech = np.clip(waveform * 32768, -32768, 32767).astype("<i2").tobytes()
    return Utterance(text, speech, speech + b"\x00\x00" * INPUT_RATE)


async def synthesize_paused_utterance(
    voice: str,
    segments: tuple[str, ...],
    *,
    pause_seconds: float,
) -> Utterance:
    provider = LocalAppleSpeechProvider(voice=voice)
    waveforms: list[np.ndarray] = []
    for index, text in enumerate(segments):
        pcm_24khz = await provider.synthesize(text)
        waveforms.append(provider._waveform(pcm_24khz, OUTPUT_RATE, 1))
        if index < len(segments) - 1:
            waveforms.append(np.zeros(round(pause_seconds * INPUT_RATE), dtype=np.float32))
    waveform = np.concatenate(waveforms)
    speech = np.clip(waveform * 32768, -32768, 32767).astype("<i2").tobytes()
    return Utterance(" ".join(segments), speech, speech + b"\x00\x00" * INPUT_RATE)


async def synthesize_background_babble() -> Utterance:
    provider = LocalAppleSpeechProvider(voice="Samantha")
    pcm_24khz = await provider.synthesize("Ba ba da da. Ma ma ba ba. Da ba ma da. Ba da ba ma.")
    waveform = provider._waveform(pcm_24khz, OUTPUT_RATE, 1).astype(np.float32)
    maximum_rms = 850 / 32767
    for start in range(0, len(waveform), FRAME_SAMPLES):
        frame = waveform[start : start + FRAME_SAMPLES]
        if not frame.size:
            continue
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        if rms > maximum_rms:
            frame *= maximum_rms / rms
    waveform = waveform[: INPUT_RATE * 2]
    speech = np.clip(waveform * 32768, -32768, 32767).astype("<i2").tobytes()
    return Utterance("nearby background babble", speech, speech + b"\x00\x00" * INPUT_RATE)


async def publish_audio(source: RecordingAudioSource, utterance: Utterance) -> tuple[float, float]:
    started_at = time.monotonic()
    next_frame_at = started_at
    frame_bytes = FRAME_SAMPLES * 2
    speech_frames = math.ceil(len(utterance.speech_pcm) / frame_bytes)
    speech_ended_at = started_at
    for frame_index, offset in enumerate(range(0, len(utterance.pcm), frame_bytes), start=1):
        chunk = utterance.pcm[offset : offset + frame_bytes].ljust(frame_bytes, b"\x00")
        await source.capture_frame(rtc.AudioFrame(chunk, INPUT_RATE, 1, FRAME_SAMPLES))
        next_frame_at += FRAME_SAMPLES / INPUT_RATE
        if frame_index == speech_frames:
            speech_ended_at = next_frame_at
        delay = next_frame_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
    return started_at, speech_ended_at


async def send_playback_acknowledgement(
    room: rtc.Room,
    acknowledgement: protocol_pb2.PlaybackAcknowledgement,
) -> None:
    packet = protocol_pb2.ClientEvent(
        protocol_version=2,
        playback_acknowledgement=acknowledgement,
    )
    await room.local_participant.publish_data(
        packet.SerializeToString(deterministic=True),
        reliable=True,
        destination_identities=["radcliffe-worker"],
    )


async def send_control(
    room: rtc.Room,
    kind: int,
    playback: ClientPlaybackTracker | None = None,
) -> None:
    if playback is not None and kind in {
        protocol_pb2.ControlCommand.KIND_INTERRUPT,
        protocol_pb2.ControlCommand.KIND_PAUSE,
        protocol_pb2.ControlCommand.KIND_STOP,
    }:
        for acknowledgement in playback.flush("control flushed synthetic audio sink"):
            await send_playback_acknowledgement(room, acknowledgement)
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


def _write_wav(path: Path, pcm: bytes, sample_rate: int, *, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)


def _output_timeline(state: SmokeState) -> bytes:
    output = tuple(item for item in state.output if item.received_at >= state.started_at)
    if not output:
        return b""
    final_sample = max(
        round((item.received_at - state.started_at) * OUTPUT_RATE) + len(item.pcm) // 2
        for item in output
    )
    timeline = np.zeros(final_sample, dtype="<i2")
    for item in output:
        start = round((item.received_at - state.started_at) * OUTPUT_RATE)
        samples = np.frombuffer(item.pcm, dtype="<i2")
        timeline[start : start + len(samples)] = samples
    return timeline.tobytes()


def _input_timeline(state: SmokeState) -> np.ndarray:
    input_audio = tuple(item for item in state.input if item.received_at >= state.started_at)
    if not input_audio:
        return np.zeros(0, dtype="<i2")
    final_sample = max(
        round((item.received_at - state.started_at) * INPUT_RATE) + len(item.pcm) // 2
        for item in input_audio
    )
    timeline = np.zeros(final_sample, dtype="<i2")
    for item in input_audio:
        start = round((item.received_at - state.started_at) * INPUT_RATE)
        samples = np.frombuffer(item.pcm, dtype="<i2")
        timeline[start : start + len(samples)] = samples
    return timeline


def _review_timeline(state: SmokeState) -> bytes:
    input_16khz = _input_timeline(state)
    if input_16khz.size:
        output_length = round(len(input_16khz) * OUTPUT_RATE / INPUT_RATE)
        input_24khz = np.interp(
            np.linspace(0, len(input_16khz) - 1, output_length),
            np.arange(len(input_16khz)),
            input_16khz.astype(np.float32),
        ).astype("<i2")
    else:
        input_24khz = np.zeros(0, dtype="<i2")
    agent = np.frombuffer(_output_timeline(state), dtype="<i2")
    length = max(len(input_24khz), len(agent))
    stereo = np.zeros((length, 2), dtype="<i2")
    stereo[: len(input_24khz), 0] = input_24khz
    stereo[: len(agent), 1] = agent
    return stereo.tobytes()


def _proto_event(item: TimedEvent, started_at: float) -> dict[str, object]:
    return {
        "received_seconds": round(item.received_at - started_at, 4),
        "event": MessageToDict(item.event, preserving_proto_field_name=True),
    }


async def run(commit: str) -> int:
    control_url = os.environ.get("RADCLIFFE_CONTROL_URL", "http://127.0.0.1:8000")
    sidecar_url = os.environ.get("RADCLIFFE_SIDECAR_URL", "http://127.0.0.1:8787")
    bootstrap_token = os.environ.get("RADCLIFFE_BOOTSTRAP_TOKEN")
    session_id = os.environ.get(
        "RADCLIFFE_CONFORMANCE_SESSION_ID",
        f"radcliffe-conformance-{uuid4().hex[:12]}",
    )
    if not bootstrap_token:
        raise RuntimeError("RADCLIFFE_BOOTSTRAP_TOKEN is required")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = ROOT / "artifacts" / "voice-conformance" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    utterances = {
        "paused": await synthesize_utterance(
            "Daniel", "This paused speech must not become a message."
        ),
        "normal": await synthesize_utterance(
            "Karen", "What color do red and blue paint make? Reply with one word."
        ),
        "turn-two": await synthesize_utterance("Moira", "Reply with exactly blue."),
        "turn-three": await synthesize_utterance("Fred", "Reply with exactly seven."),
        "turn-four": await synthesize_utterance("Daniel", "Reply with exactly finished."),
        "long-pause-replacement": await synthesize_paused_utterance(
            "Fred",
            (
                "Tell me a dirty joke.",
                "Actually, ignore that. Reply with exactly changed subject.",
            ),
            pause_seconds=1.2,
        ),
        "read-source": await synthesize_utterance(
            "Karen",
            "Use the read source tool to read radcliffe dash demo dot M D word for word.",
        ),
        "cosmic": await synthesize_utterance(
            "Samantha",
            "Please start your response exactly with dash, bold Cosmic touch, "
            "then give me a moon fact.",
        ),
        "interrupt": await synthesize_utterance(
            "Tessa", "Tell me a five sentence story about a lighthouse."
        ),
        "speech-barge": await synthesize_utterance(
            "Daniel",
            "Count slowly from one to two hundred, spelling every number in words, "
            "and do not stop early.",
        ),
        "replacement": await synthesize_utterance(
            "Samantha", "Stop counting. Reply with exactly switched topic."
        ),
        "background-babble": await synthesize_background_babble(),
    }
    for name, utterance in utterances.items():
        _write_wav(output_dir / f"user-{name}.wav", utterance.pcm, INPUT_RATE)

    async with httpx.AsyncClient(timeout=10) as client:
        worker_response = await client.post(
            f"{control_url.rstrip('/')}/v1/sessions",
            headers={"Authorization": f"Bearer {bootstrap_token}"},
            json={
                "session_id": session_id,
                "participant_identity": "radcliffe-worker",
                "role": "worker",
            },
        )
        worker_response.raise_for_status()
        worker_credentials = worker_response.json()
        response = await client.post(
            f"{control_url.rstrip('/')}/v1/sessions",
            headers={"Authorization": f"Bearer {bootstrap_token}"},
            json={
                "session_id": session_id,
                "participant_identity": "synthetic-voice-conformance",
                "role": "client",
            },
        )
        response.raise_for_status()
        credentials = response.json()

    worker_environment = {
        **os.environ,
        "RADCLIFFE_SESSION_ID": session_id,
        "RADCLIFFE_LIVEKIT_URL": worker_credentials["livekit_url"],
        "RADCLIFFE_LIVEKIT_TOKEN": worker_credentials["participant_token"],
        "RADCLIFFE_DATABASE_PATH": str(output_dir / "events.sqlite3"),
    }
    worker = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "python",
        "-m",
        "radcliffe_media.production",
        cwd=ROOT,
        env=worker_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    worker_log = bytearray()

    async def capture_worker_log() -> None:
        assert worker.stdout is not None
        while chunk := await worker.stdout.read(8192):
            worker_log.extend(chunk)

    worker_log_task = asyncio.create_task(capture_worker_log())

    room = rtc.Room()
    state = SmokeState()
    playback = ClientPlaybackTracker()
    checks: list[Check] = []
    cancelled_user_message_id: str | None = None
    run_error: Exception | None = None

    async def capture(track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=OUTPUT_RATE, num_channels=1)
        try:
            async for event in stream:
                pcm = bytes(event.frame.data)
                state.output.append(TimedAudio(time.monotonic(), pcm))
                for acknowledgement in playback.audio_frame(pcm, OUTPUT_RATE):
                    await send_playback_acknowledgement(room, acknowledgement)
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
            state.events.append(TimedEvent(time.monotonic(), event))
            if event.HasField("playback_segment"):
                playback.announce(event.playback_segment)

    try:
        await room.connect(credentials["livekit_url"], credentials["participant_token"])
        await wait_until(lambda: "radcliffe-worker" in room.remote_participants, 30)
        state.started_at = time.monotonic()
        livekit_source = rtc.AudioSource(INPUT_RATE, 1)
        source = RecordingAudioSource(livekit_source, state)
        track = rtc.LocalAudioTrack.create_audio_track("synthetic-microphone", livekit_source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.Value("SOURCE_MICROPHONE")
        await room.local_participant.publish_track(track, options)
        await asyncio.sleep(0.5)

        await send_control(room, protocol_pb2.ControlCommand.KIND_PAUSE, playback)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    0,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_PAUSED
                    ),
                )
                is not None
            )
        )
        message_count = sum(item.event.HasField("message") for item in state.events)
        await publish_audio(source, utterances["paused"])
        await asyncio.sleep(1)
        pause_blocked = (
            sum(item.event.HasField("message") for item in state.events) == message_count
        )
        checks.append(
            Check(
                "pause_suppresses_microphone_turn",
                pause_blocked,
                "paused synthetic speech produced no envelope"
                if pause_blocked
                else "paused synthetic speech produced an envelope",
            )
        )

        await send_control(room, protocol_pb2.ControlCommand.KIND_RESUME, playback)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    0,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_LISTENING
                        and event.state_changed.reason == "resumed"
                    ),
                )
                is not None
            )
        )

        normal_index = len(state.events)
        _, normal_speech_end = await publish_audio(source, utterances["normal"])
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    normal_index,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_USER
                    ),
                )
                is not None
            ),
            20,
        )
        normal_user = _event_after(
            state,
            normal_index,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_USER
            ),
        )
        assert normal_user is not None
        normal_text = normal_user.event.message.content.inline_text
        normal_quality = assess_transcript(
            utterances["normal"].text,
            normal_text,
            ("red", "blue", "paint"),
        )
        checks.append(
            Check(
                "normal_turn_transcript",
                normal_quality.passed,
                f"transcribed: {normal_text!r}",
                {
                    "word_error_rate": round(normal_quality.word_error_rate, 4),
                    "keyword_recall": round(normal_quality.keyword_recall, 4),
                },
            )
        )
        normal_audio_onset = await wait_for_audio_after(state, normal_speech_end, 30)
        first_audio_seconds = normal_audio_onset - normal_speech_end
        checks.append(
            Check(
                "normal_turn_first_audio",
                first_audio_seconds < 5,
                f"first heard audio {first_audio_seconds:.3f}s after synthetic speech ended",
                {"latency_seconds": round(first_audio_seconds, 3)},
            )
        )
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    normal_index,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
                    ),
                )
                is not None
            ),
            30,
        )
        normal_model = _event_after(
            state,
            normal_index,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
            ),
        )
        assert normal_model is not None
        normal_model_text = normal_model.event.message.content.inline_text.casefold()
        checks.append(
            Check(
                "normal_turn_model_content",
                "purple" in normal_model_text,
                f"model message: {normal_model_text!r}",
            )
        )
        await wait_for_output_silence(state, after=normal_audio_onset, limit_seconds=30)

        for turn_number, (name, expected) in enumerate(
            (
                ("turn-two", "blue"),
                ("turn-three", "seven"),
                ("turn-four", "finished"),
            ),
            start=2,
        ):
            turn_index = len(state.events)
            _, speech_end = await publish_audio(source, utterances[name])
            await wait_until(
                lambda turn_index=turn_index: (
                    _event_after(
                        state,
                        turn_index,
                        lambda event: (
                            event.HasField("message")
                            and event.message.source == protocol_pb2.SOURCE_KIND_USER
                        ),
                    )
                    is not None
                ),
                20,
            )
            audio_onset = await wait_for_audio_after(state, speech_end, 30)
            latency = audio_onset - speech_end
            checks.append(
                Check(
                    f"turn_{turn_number}_first_audio",
                    latency < 5,
                    f"first heard audio {latency:.3f}s after synthetic speech ended",
                    {"latency_seconds": round(latency, 3)},
                )
            )
            await wait_until(
                lambda turn_index=turn_index: (
                    _event_after(
                        state,
                        turn_index,
                        lambda event: (
                            event.HasField("message")
                            and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
                        ),
                    )
                    is not None
                ),
                30,
            )
            model = _event_after(
                state,
                turn_index,
                lambda event: (
                    event.HasField("message")
                    and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
                ),
            )
            assert model is not None
            model_text = model.event.message.content.inline_text.casefold()
            checks.append(
                Check(
                    f"turn_{turn_number}_model_content",
                    expected in model_text,
                    f"expected {expected!r} in model message: {model_text!r}",
                )
            )
            await wait_for_output_silence(state, after=audio_onset, limit_seconds=30)

        long_pause_index = len(state.events)
        long_pause_started, long_pause_speech_end = await publish_audio(
            source,
            utterances["long-pause-replacement"],
        )
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    long_pause_index,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_USER
                        and "subject" in event.message.content.inline_text.casefold()
                    ),
                )
                is not None
            ),
            30,
        )
        long_pause_user = _event_after(
            state,
            long_pause_index,
            lambda event: (
                event.HasField("message")
                and event.message.source == protocol_pb2.SOURCE_KIND_USER
                and "subject" in event.message.content.inline_text.casefold()
            ),
        )
        assert long_pause_user is not None
        long_pause_user_index = state.events.index(long_pause_user)
        long_pause_audio = await wait_for_audio_after(state, long_pause_speech_end, 30)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    long_pause_user_index + 1,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
                    ),
                )
                is not None
            ),
            30,
        )
        long_pause_models = [
            item.event.message.content.inline_text.casefold()
            for item in state.events[long_pause_index:]
            if item.event.HasField("message")
            and item.event.message.source == protocol_pb2.SOURCE_KIND_MODEL
        ]
        early_long_pause_audio = [
            item
            for item in state.output
            if long_pause_started <= item.received_at < long_pause_speech_end and _active(item.pcm)
        ]
        checks.append(
            Check(
                "long_pause_does_not_launch_an_early_answer",
                not early_long_pause_audio,
                f"heard {len(early_long_pause_audio)} active audio frames before replacement ended",
            )
        )
        checks.append(
            Check(
                "long_pause_replacement_has_one_model_owner",
                len(long_pause_models) == 1
                and "subject" in long_pause_models[0]
                and "joke" not in long_pause_models[0],
                f"model messages after long pause: {long_pause_models!r}",
            )
        )
        long_pause_latency = long_pause_audio - long_pause_speech_end
        checks.append(
            Check(
                "long_pause_replacement_first_audio",
                long_pause_latency < 5,
                f"first heard replacement audio {long_pause_latency:.3f}s after speech ended",
                {"latency_seconds": round(long_pause_latency, 3)},
            )
        )
        await wait_for_output_silence(state, after=long_pause_audio, limit_seconds=30)

        source_index = len(state.events)
        _, source_speech_end = await publish_audio(source, utterances["read-source"])
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    source_index,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_TEXT_SOURCE
                    ),
                )
                is not None
            ),
            30,
        )
        source_message = _event_after(
            state,
            source_index,
            lambda event: (
                event.HasField("message")
                and event.message.source == protocol_pb2.SOURCE_KIND_TEXT_SOURCE
            ),
        )
        assert source_message is not None
        canonical_source = (ROOT / "fixtures/sources/radcliffe-demo.md").read_bytes()
        source_envelope = source_message.event.message
        source_bytes = source_envelope.content.inline_text.encode()
        source_hash = hashlib.sha256(canonical_source).digest()
        canonical_envelope_matches = (
            source_bytes == canonical_source
            and source_envelope.content_sha256 == source_hash
            and source_envelope.provenance.source_sha256 == source_hash
            and source_envelope.provenance.display_name == "radcliffe-demo.md"
        )
        checks.append(
            Check(
                "source_tool_preserves_canonical_envelope",
                canonical_envelope_matches,
                (
                    "received exact fixture bytes with canonical name and matching hashes"
                    if canonical_envelope_matches
                    else "canonical bytes, hashes, or display name did not match the fixture"
                ),
                {
                    "byte_length": len(source_bytes),
                    "sha256": source_envelope.content_sha256.hex(),
                    "display_name": source_envelope.provenance.display_name,
                },
            )
        )
        reading_state = _event_after(
            state,
            source_index,
            lambda event: (
                event.HasField("state_changed")
                and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_READING
            ),
        )
        checks.append(
            Check(
                "source_tool_enters_reading_state",
                reading_state is not None,
                "worker announced deterministic source reading"
                if reading_state is not None
                else "worker never entered source reading",
            )
        )
        source_audio = await wait_for_audio_after(state, source_speech_end, 30)
        source_latency = source_audio - source_speech_end
        checks.append(
            Check(
                "source_tool_first_audio",
                source_latency < 5,
                f"first source audio arrived {source_latency:.3f}s after speech ended",
                {"latency_seconds": round(source_latency, 3)},
            )
        )
        await asyncio.sleep(0.8)
        source_pause_started = time.monotonic()
        await send_control(room, protocol_pb2.ControlCommand.KIND_PAUSE, playback)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    source_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_PAUSED
                        and event.state_changed.reason == "paused"
                    ),
                )
                is not None
            ),
            5,
        )
        source_pause_event = _event_after(
            state,
            source_index,
            lambda event: (
                event.HasField("state_changed")
                and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_PAUSED
                and event.state_changed.reason == "paused"
            ),
        )
        assert source_pause_event is not None
        source_pause_ack = source_pause_event.received_at - source_pause_started
        await wait_for_output_silence(
            state,
            after=source_pause_started,
            silence_seconds=0.5,
            limit_seconds=5,
        )
        source_audio_after_pause = [
            item.received_at
            for item in state.output
            if source_pause_started < item.received_at <= source_pause_started + 1.2
            and _active(item.pcm)
        ]
        source_pause_stop = (
            max(source_audio_after_pause) - source_pause_started if source_audio_after_pause else 0
        )
        checks.append(
            Check(
                "source_pause_stops_heard_audio",
                source_pause_ack < 0.3 and source_pause_stop < 0.5,
                (
                    f"pause acknowledged in {source_pause_ack:.3f}s; "
                    f"last source audio at {source_pause_stop:.3f}s"
                ),
                {
                    "acknowledgement_seconds": round(source_pause_ack, 3),
                    "stop_seconds": round(source_pause_stop, 3),
                },
            )
        )

        source_resume_index = len(state.events)
        source_resume_started = time.monotonic()
        await send_control(room, protocol_pb2.ControlCommand.KIND_RESUME, playback)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    source_resume_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_READING
                        and event.state_changed.reason == "resumed"
                    ),
                )
                is not None
            ),
            5,
        )
        resumed_source_audio = await wait_for_audio_after(state, source_resume_started, 10)
        source_resume_latency = resumed_source_audio - source_resume_started
        checks.append(
            Check(
                "source_resume_returns_to_reading",
                source_resume_latency < 3,
                f"source audio resumed from its persisted boundary in {source_resume_latency:.3f}s",
                {"latency_seconds": round(source_resume_latency, 3)},
            )
        )
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    source_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_LISTENING
                        and event.state_changed.reason == "turn complete"
                    ),
                )
                is not None
            ),
            60,
        )
        source_models = [
            item.event.message.content.inline_text
            for item in state.events[source_index:]
            if item.event.HasField("message")
            and item.event.message.source == protocol_pb2.SOURCE_KIND_MODEL
        ]
        checks.append(
            Check(
                "source_bytes_bypass_model_generation",
                all(canonical_source.decode() not in text for text in source_models),
                f"post-tool model messages: {source_models!r}",
            )
        )
        await wait_for_output_silence(state, after=resumed_source_audio, limit_seconds=60)

        cosmic_index = len(state.events)
        await publish_audio(source, utterances["cosmic"])
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    cosmic_index,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_USER
                    ),
                )
                is not None
            ),
            20,
        )
        cosmic_user = _event_after(
            state,
            cosmic_index,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_USER
            ),
        )
        assert cosmic_user is not None
        cosmic_text = cosmic_user.event.message.content.inline_text
        cosmic_quality = assess_transcript(
            utterances["cosmic"].text,
            cosmic_text,
            ("dash", "bold", "Cosmic touch", "moon"),
        )
        checks.append(
            Check(
                "cosmic_command_transcript",
                cosmic_quality.passed,
                f"transcribed: {cosmic_text!r}",
                {
                    "word_error_rate": round(cosmic_quality.word_error_rate, 4),
                    "keyword_recall": round(cosmic_quality.keyword_recall, 4),
                },
            )
        )
        await send_control(room, protocol_pb2.ControlCommand.KIND_INTERRUPT, playback)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    cosmic_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.reason == "interrupted"
                    ),
                )
                is not None
            )
        )
        await wait_for_output_silence(state, after=time.monotonic(), limit_seconds=5)

        interrupt_index = len(state.events)
        _, story_speech_end = await publish_audio(source, utterances["interrupt"])
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    interrupt_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_SPEAKING
                    ),
                )
                is not None
            ),
            30,
        )
        story_audio = await wait_for_audio_after(state, time.monotonic() - 1, 20)
        story_first_audio_seconds = story_audio - story_speech_end
        checks.append(
            Check(
                "second_turn_first_audio",
                story_first_audio_seconds < 5,
                f"first heard audio {story_first_audio_seconds:.3f}s after synthetic speech ended",
                {"latency_seconds": round(story_first_audio_seconds, 3)},
            )
        )
        interrupt_started = time.monotonic()
        await send_control(room, protocol_pb2.ControlCommand.KIND_INTERRUPT, playback)
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    interrupt_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.reason == "interrupted"
                    ),
                )
                is not None
            )
        )
        interrupt_event = _event_after(
            state,
            interrupt_index,
            lambda event: (
                event.HasField("state_changed") and event.state_changed.reason == "interrupted"
            ),
        )
        assert interrupt_event is not None
        interrupt_ack_seconds = interrupt_event.received_at - interrupt_started
        await asyncio.sleep(1.2)
        audible_after_interrupt = [
            item.received_at
            for item in state.output
            if item.received_at > interrupt_started and _active(item.pcm)
        ]
        audible_stop_seconds = (
            max(audible_after_interrupt) - interrupt_started if audible_after_interrupt else 0
        )
        checks.append(
            Check(
                "interrupt_acknowledgement",
                interrupt_ack_seconds < 0.5,
                f"interrupt acknowledged in {interrupt_ack_seconds:.3f}s",
                {"latency_seconds": round(interrupt_ack_seconds, 3)},
            )
        )
        checks.append(
            Check(
                "interrupt_stops_heard_audio",
                audible_stop_seconds <= 1.2,
                f"last heard audio was {audible_stop_seconds:.3f}s after interrupt",
                {
                    "stop_seconds": round(audible_stop_seconds, 3),
                    "story_audio_started_seconds": round(story_audio - state.started_at, 3),
                },
            )
        )

        async with httpx.AsyncClient(timeout=5) as sidecar_before:
            sidecar_baseline_response = await sidecar_before.get(f"{sidecar_url}/healthz")
            sidecar_baseline_response.raise_for_status()
            sidecar_baseline = sidecar_baseline_response.json()
        cancelled_before = int(sidecar_baseline.get("cancelled_turns", 0))
        barge_index = len(state.events)
        _, barge_speech_end = await publish_audio(source, utterances["speech-barge"])
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    barge_index,
                    lambda event: (
                        event.HasField("state_changed")
                        and event.state_changed.current == protocol_pb2.CONVERSATION_STATE_SPEAKING
                    ),
                )
                is not None
            ),
            30,
        )
        barge_user = _event_after(
            state,
            barge_index,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_USER
            ),
        )
        assert barge_user is not None
        cancelled_user_message_id = barge_user.event.message.message_id
        await wait_for_audio_after(state, barge_speech_end, 30)
        async with httpx.AsyncClient(timeout=5) as sidecar_client:
            active_response = await sidecar_client.get(f"{sidecar_url}/healthz")
            active_response.raise_for_status()
            active_sidecar_turns = int(active_response.json().get("active_turns", 0))
        checks.append(
            Check(
                "speech_replacement_observes_live_sidecar_stream",
                active_sidecar_turns >= 1,
                f"sidecar reported {active_sidecar_turns} active turn(s) before barge-in",
            )
        )

        babble_index = len(state.events)
        babble_started, babble_ended = await publish_audio(source, utterances["background-babble"])
        await asyncio.sleep(0.25)
        babble_interruption = _event_after(
            state,
            babble_index,
            lambda event: (
                event.HasField("state_changed") and event.state_changed.reason == "interrupted"
            ),
        )
        babble_user = _event_after(
            state,
            babble_index,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_USER
            ),
        )
        heard_during_babble = [
            item
            for item in state.output
            if babble_started + 0.25 <= item.received_at <= babble_ended and _active(item.pcm)
        ]
        async with httpx.AsyncClient(timeout=5) as sidecar_during_babble:
            active_after_babble_response = await sidecar_during_babble.get(f"{sidecar_url}/healthz")
            active_after_babble_response.raise_for_status()
            active_after_babble = int(active_after_babble_response.json().get("active_turns", 0))
        checks.append(
            Check(
                "background_babble_does_not_interrupt_or_create_turn",
                babble_interruption is None and babble_user is None,
                (
                    "background babble produced neither interruption nor user envelope"
                    if babble_interruption is None and babble_user is None
                    else "background babble interrupted playback or created a user envelope"
                ),
                {
                    "babble_seconds": round(babble_ended - babble_started, 3),
                    "interrupted": babble_interruption is not None,
                    "created_user_message": babble_user is not None,
                },
            )
        )
        checks.append(
            Check(
                "background_babble_preserves_agent_audio",
                bool(heard_during_babble) and active_after_babble >= 1,
                (
                    f"heard {len(heard_during_babble)} active assistant frames during babble; "
                    f"sidecar active turns={active_after_babble}"
                    if heard_during_babble and active_after_babble >= 1
                    else "assistant became inaudible or its active turn ended during babble"
                ),
                {
                    "active_output_frames": len(heard_during_babble),
                    "active_turns_after_babble": active_after_babble,
                },
            )
        )

        replacement_index = len(state.events)
        replacement_started = time.monotonic()
        _, replacement_speech_end = await publish_audio(source, utterances["replacement"])
        speech_interrupt_event = _event_after(
            state,
            replacement_index,
            lambda event: (
                event.HasField("state_changed") and event.state_changed.reason == "interrupted"
            ),
        )
        speech_interrupt_ack = (
            speech_interrupt_event.received_at - replacement_started
            if speech_interrupt_event is not None
            else 30.0
        )
        checks.append(
            Check(
                "speech_barge_acknowledgement",
                speech_interrupt_ack < 0.5,
                f"speech interruption acknowledged in {speech_interrupt_ack:.3f}s",
                {"latency_seconds": round(speech_interrupt_ack, 3)},
            )
        )
        old_audio_after_barge = [
            item.received_at
            for item in state.output
            if replacement_started < item.received_at <= replacement_started + 1.2
            and _active(item.pcm)
        ]
        speech_barge_stop_seconds = (
            max(old_audio_after_barge) - replacement_started if old_audio_after_barge else 0
        )
        checks.append(
            Check(
                "speech_barge_stops_heard_audio",
                speech_barge_stop_seconds <= 0.5,
                f"last old-turn audio was {speech_barge_stop_seconds:.3f}s after new speech",
                {"stop_seconds": round(speech_barge_stop_seconds, 3)},
            )
        )
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    replacement_index,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_USER
                    ),
                )
                is not None
            ),
            20,
        )
        replacement_user = _event_after(
            state,
            replacement_index,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_USER
            ),
        )
        assert replacement_user is not None
        replacement_user_index = state.events.index(replacement_user)
        replacement_audio = await wait_for_audio_after(state, replacement_speech_end, 30)
        replacement_latency = replacement_audio - replacement_speech_end
        await wait_until(
            lambda: (
                _event_after(
                    state,
                    replacement_user_index + 1,
                    lambda event: (
                        event.HasField("message")
                        and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
                    ),
                )
                is not None
            ),
            30,
        )
        replacement_model = _event_after(
            state,
            replacement_user_index + 1,
            lambda event: (
                event.HasField("message") and event.message.source == protocol_pb2.SOURCE_KIND_MODEL
            ),
        )
        assert replacement_model is not None
        replacement_model_text = replacement_model.event.message.content.inline_text.casefold()
        checks.append(
            Check(
                "replacement_turn_does_not_resume_abandoned_count",
                "switched topic" in replacement_model_text
                and "one, two" not in replacement_model_text,
                f"replacement model message: {replacement_model_text!r}",
            )
        )
        checks.append(
            Check(
                "replacement_turn_first_audio",
                replacement_latency < 5,
                f"first heard replacement audio after {replacement_latency:.3f}s",
                {"latency_seconds": round(replacement_latency, 3)},
            )
        )
        await wait_for_output_silence(state, after=replacement_audio, limit_seconds=30)
        async with httpx.AsyncClient(timeout=5) as sidecar_client:
            async with asyncio.timeout(10):
                while True:  # noqa: ASYNC110 - polling the external sidecar process
                    sidecar_after_response = await sidecar_client.get(f"{sidecar_url}/healthz")
                    sidecar_after_response.raise_for_status()
                    sidecar_after = sidecar_after_response.json()
                    if int(sidecar_after.get("active_turns", 0)) == 0:
                        break
                    await asyncio.sleep(0.05)
        cancelled_after = int(sidecar_after.get("cancelled_turns", 0))
        checks.append(
            Check(
                "speech_replacement_closes_abandoned_http_stream",
                cancelled_after > cancelled_before,
                f"sidecar cancellation counter advanced {cancelled_before} -> {cancelled_after}",
            )
        )
    except Exception as error:
        run_error = error
        checks.append(
            Check(
                "scenario_execution",
                False,
                f"{type(error).__name__}: {error}",
            )
        )
    finally:
        await room.disconnect()
        for task in state.capture_tasks:
            task.cancel()
        await asyncio.gather(*state.capture_tasks, return_exceptions=True)
        if worker.returncode is None:
            worker.terminate()
        try:
            await asyncio.wait_for(worker.wait(), timeout=10)
        except TimeoutError:
            worker.kill()
            await worker.wait()
        await worker_log_task
        async with httpx.AsyncClient(timeout=10) as client:
            archive = await client.patch(
                f"{control_url.rstrip('/')}/v1/sessions/{session_id}",
                headers={"Authorization": f"Bearer {bootstrap_token}"},
                json={"status": "archived"},
            )
            archive.raise_for_status()

    envelopes = await EventStore(output_dir / "events.sqlite3").replay(session_id)
    packets = context_replay(envelopes)
    emoji_model_messages = [
        item.message_id
        for item in envelopes
        if item.source.value == "model"
        and item.content.inline_text is not None
        and _contains_emoji(item.content.inline_text)
    ]
    checks.append(
        Check(
            "model_responses_contain_no_emoji",
            not emoji_model_messages,
            (
                "all model responses complied with the no-emoji system instruction"
                if not emoji_model_messages
                else f"emoji appeared in model messages: {emoji_model_messages!r}"
            ),
        )
    )
    source_envelopes = [item for item in envelopes if item.source.value == "text_source"]
    if source_envelopes:
        source_message_id = source_envelopes[0].message_id
        with sqlite3.connect(output_dir / "events.sqlite3") as database:
            playback_rows = database.execute(
                """SELECT playback_id, segment_id, status, boundary_index
                FROM playback_acknowledgements
                WHERE session_id = ? AND segment_id LIKE ?
                ORDER BY rowid""",
                (session_id, f"{source_message_id}:%"),
            ).fetchall()
        first_segment = f"{source_message_id}:0"
        started_first = sum(
            segment_id == first_segment and status == "started"
            for _playback_id, segment_id, status, _boundary in playback_rows
        )
        completed_boundary = max(
            (
                int(boundary)
                for _playback_id, _segment_id, status, boundary in playback_rows
                if status == "completed"
            ),
            default=0,
        )
        client_playback_ids = {acknowledgement.playback_id for acknowledgement in playback.sent}
        persisted_playback_ids = {
            playback_id for playback_id, _segment_id, _status, _boundary in playback_rows
        }
        checks.append(
            Check(
                "source_resume_uses_persisted_playback_boundary",
                len(source_envelopes) == 1
                and started_first == 2
                and any(status == "flushed" for _, _, status, _ in playback_rows)
                and completed_boundary == 4,
                (
                    f"source envelopes={len(source_envelopes)}, first starts={started_first}, "
                    f"final boundary={completed_boundary}, ledger={playback_rows!r}"
                ),
            )
        )
        checks.append(
            Check(
                "source_playback_is_device_acknowledged",
                bool(persisted_playback_ids) and persisted_playback_ids <= client_playback_ids,
                (
                    "every persisted source playback attempt came from the decoded-audio client"
                    if persisted_playback_ids and persisted_playback_ids <= client_playback_ids
                    else (
                        f"persisted={sorted(persisted_playback_ids)!r}, "
                        f"client={sorted(client_playback_ids)!r}"
                    )
                ),
            )
        )
    if cancelled_user_message_id is not None:
        cancelled_envelope = next(
            item for item in envelopes if item.message_id == cancelled_user_message_id
        )
        turn_id = cancelled_envelope.metadata.get(TURN_ID_METADATA)
        disposition = next(
            (
                item.metadata.get(TURN_DISPOSITION_METADATA)
                for item in envelopes
                if item.metadata.get(TURN_EVENT_METADATA) == "turn_disposition"
                and item.metadata.get(TURN_ID_METADATA) == turn_id
            ),
            None,
        )
        packet = next(item for item in packets if item.message_id == cancelled_user_message_id)
        checks.append(
            Check(
                "abandoned_turn_is_auditable_but_excluded_from_context",
                disposition in {"interrupted", "superseded"} and not packet.include,
                f"durable disposition={disposition!r}, context include={packet.include}",
            )
        )

    _write_wav(output_dir / "agent-timeline.wav", _output_timeline(state), OUTPUT_RATE)
    _write_wav(
        output_dir / "review-timeline-stereo.wav",
        _review_timeline(state),
        OUTPUT_RATE,
        channels=2,
    )
    (output_dir / "worker.log").write_bytes(worker_log)
    report = {
        "schema": "radcliffe-livekit-conformance-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "session_id": session_id,
        "passed": all(check.passed for check in checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
                "measured": check.measured,
            }
            for check in checks
        ],
        "events": [_proto_event(item, state.started_at) for item in state.events],
        "artifacts": {
            "agent_timeline": "agent-timeline.wav",
            "review_timeline": "review-timeline-stereo.wav",
            "review_channels": {"left": "synthetic microphone", "right": "assistant"},
            "user_inputs": [f"user-{name}.wav" for name in utterances],
            "worker_log": "worker.log",
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "passed": report["passed"],
                "session_id": session_id,
                "checks": report["checks"],
            },
            indent=2,
        )
    )
    return 0 if report["passed"] and run_error is None else 1


def main() -> int:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return asyncio.run(run(commit))


if __name__ == "__main__":
    raise SystemExit(main())
