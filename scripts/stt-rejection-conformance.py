#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import subprocess
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from pipecat.frames.frames import UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from radcliffe_media.production import EndpointProcessor, EndpointSettings
from radcliffe_media.speech import LocalAppleSpeechProvider, Transcription
from radcliffe_media.speech_activity import SileroSpeechActivityAnalyzer

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000
FRAME_SAMPLES = 320


@dataclass(frozen=True, slots=True)
class CorpusCase:
    case_id: str
    samples: np.ndarray
    should_accept: bool
    expected_text: str = ""
    route: str = "synthetic"


class RecordingSpeech(LocalAppleSpeechProvider):
    def __init__(self) -> None:
        super().__init__()
        self.transcription_calls = 0

    async def transcribe_with_evidence(
        self, pcm: bytes, sample_rate: int, channels: int
    ) -> Transcription:
        self.transcription_calls += 1
        return await super().transcribe_with_evidence(pcm, sample_rate, channels)


class CaptureVoice:
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.clarifications: list[str] = []

    async def handle_transcript(self, transcript: str) -> None:
        self.transcripts.append(transcript)

    async def barge_in(self) -> None:
        return None

    def foreground_turn_active(self) -> bool:
        return False

    async def request_clarification(self, reason: str) -> None:
        self.clarifications.append(reason)


def _pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def _write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(_pcm(samples))


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _cap_frame_rms(samples: np.ndarray, maximum_rms: float) -> np.ndarray:
    """Keep a realistic waveform while placing every frame below a foreground mic level."""
    result = samples.astype(np.float32).copy()
    for start in range(0, len(result), FRAME_SAMPLES):
        frame = result[start : start + FRAME_SAMPLES]
        if not frame.size:
            continue
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        if rms > maximum_rms:
            frame *= maximum_rms / rms
    return result


def _trim_speech(samples: np.ndarray) -> np.ndarray:
    active = np.flatnonzero(np.abs(samples) > 0.001)
    if not active.size:
        return samples
    padding = round(SAMPLE_RATE * 0.01)
    start = max(0, int(active[0]) - padding)
    end = min(len(samples), int(active[-1]) + padding + 1)
    return samples[start:end]


async def _corpus() -> tuple[CorpusCase, ...]:
    random = np.random.default_rng(20260714)
    room_tone = random.normal(0, 0.002, SAMPLE_RATE * 2).astype(np.float32)
    sparse_short = np.concatenate(
        [
            random.normal(0, 0.04, round(SAMPLE_RATE * 0.344)).astype(np.float32),
            np.zeros(round(SAMPLE_RATE * 1.4), dtype=np.float32),
        ]
    )
    sparse_maximum = np.zeros(SAMPLE_RATE * 30, dtype=np.float32)
    for start in range(0, len(sparse_maximum), round(SAMPLE_RATE * 0.8)):
        end = min(start + FRAME_SAMPLES, len(sparse_maximum))
        sparse_maximum[start:end] = random.normal(0, 0.04, end - start)

    echo_provider = LocalAppleSpeechProvider(voice="Samantha")
    echo_pcm = await echo_provider.synthesize(
        "The assistant is speaking through the phone speaker."
    )
    echo = echo_provider._waveform(echo_pcm, 24000, 1) * 0.06
    echo += random.normal(0, 0.001, len(echo)).astype(np.float32)

    babble_provider = LocalAppleSpeechProvider(voice="Samantha")
    babble_pcm = await babble_provider.synthesize(
        "Ba ba da da. Ma ma ba ba. Da ba ma da. Ba da ba ma."
    )
    babble = babble_provider._waveform(babble_pcm, 24000, 1)
    babble = _cap_frame_rms(babble, 850 / 32767)
    babble = np.tile(babble, math.ceil(SAMPLE_RATE * 6 / len(babble)))[: SAMPLE_RATE * 6]
    babble += random.normal(0, 0.001, len(babble)).astype(np.float32)

    foreground_provider = LocalAppleSpeechProvider(voice="Karen")
    foreground_pcm = await foreground_provider.synthesize("Reply with exactly noise handled.")
    foreground = foreground_provider._waveform(foreground_pcm, 24000, 1)
    foreground_over_babble = babble[: len(foreground) + SAMPLE_RATE * 3].copy()
    foreground_over_babble[: len(foreground)] += foreground

    acknowledgement_provider = LocalAppleSpeechProvider(voice="Reed (English (US))")
    acknowledgement_pcm = await acknowledgement_provider.synthesize("OK.")
    acknowledgement = _trim_speech(
        acknowledgement_provider._waveform(acknowledgement_pcm, 24000, 1)
    )
    quiet_acknowledgement = _cap_frame_rms(acknowledgement, 550 / 32767)
    count_pcm = await acknowledgement_provider.synthesize("Count from one to fifty.")
    count_command = _trim_speech(acknowledgement_provider._waveform(count_pcm, 24000, 1))
    quiet_onset_command = np.concatenate([quiet_acknowledgement, count_command])

    cases: list[CorpusCase] = [
        CorpusCase("digital-silence", np.zeros(SAMPLE_RATE * 2, dtype=np.float32), False),
        CorpusCase("room-tone", room_tone, False),
        CorpusCase("post-aec-playback-echo", echo.astype(np.float32), False),
        CorpusCase("nearby-background-babble", babble, False),
        CorpusCase(
            "foreground-speech-over-continuing-babble",
            foreground_over_babble,
            True,
            "Reply with exactly noise handled.",
        ),
        CorpusCase(
            "quiet-onset-ok-us-male",
            quiet_onset_command,
            True,
            "Okay",
        ),
        CorpusCase("sparse-noise-short", sparse_short, False),
        CorpusCase("sparse-spikes-30s", sparse_maximum, False),
    ]
    for voice, phrase in (
        ("Samantha", "Go."),
        ("Fred", "Stop."),
        ("Moira", "No."),
        ("Karen", "Yes."),
        ("Samantha", "Stop."),
        ("Samantha", "Thank you."),
    ):
        provider = LocalAppleSpeechProvider(voice=voice)
        audio = await provider.synthesize(phrase)
        samples = provider._waveform(audio, 24000, 1)
        cases.append(
            CorpusCase(
                "short-earbud-thank-you"
                if phrase == "Thank you."
                else f"short-{voice.casefold()}-{_normalize(phrase).replace(' ', '-')}",
                samples,
                True,
                phrase,
                "earbud" if phrase == "Thank you." else "synthetic",
            )
        )
    speaker_provider = LocalAppleSpeechProvider(voice="Fred")
    speaker_pcm = await speaker_provider.synthesize("Thank you.")
    near_field = speaker_provider._waveform(speaker_pcm, 24000, 1)
    delay = round(SAMPLE_RATE * 0.07)
    speaker_route = near_field.copy()
    speaker_route[delay:] += near_field[:-delay] * 0.2
    speaker_route += random.normal(0, 0.0015, len(speaker_route)).astype(np.float32)
    cases.append(
        CorpusCase(
            "short-speaker-thank-you",
            speaker_route,
            True,
            "Thank you.",
            "speaker",
        )
    )
    return tuple(cases)


async def _run_case(case: CorpusCase, output: Path) -> dict[str, object]:
    trailing = np.zeros(round(SAMPLE_RATE * 1.0), dtype=np.float32)
    samples = np.concatenate([case.samples, trailing])
    remainder = len(samples) % FRAME_SAMPLES
    if remainder:
        samples = np.pad(samples, (0, FRAME_SAMPLES - remainder))
    audio_name = f"{case.case_id}.wav"
    _write_wav(output / audio_name, samples)

    speech = RecordingSpeech()
    voice = CaptureVoice()
    settings = EndpointSettings()
    endpoint = EndpointProcessor(
        speech,
        settings,
        SileroSpeechActivityAnalyzer(speech_probability=settings.speech_probability),
    )
    endpoint.voice = voice  # type: ignore[assignment]
    pcm = _pcm(samples)
    for start in range(0, len(pcm), FRAME_SAMPLES * 2):
        await endpoint.process_frame(
            UserAudioRawFrame(pcm[start : start + FRAME_SAMPLES * 2], SAMPLE_RATE, 1),
            FrameDirection.DOWNSTREAM,
        )
    task = endpoint.turn_task
    if task is not None:
        await task

    transcript = " ".join(voice.transcripts)
    accepted = bool(transcript)
    text_match = not case.should_accept or _normalize(case.expected_text) in _normalize(transcript)
    passed = accepted == case.should_accept and text_match
    return {
        "id": case.case_id,
        "expected_accept": case.should_accept,
        "accepted": accepted,
        "expected_text": case.expected_text,
        "route": case.route,
        "transcript": transcript,
        "transcription_calls": speech.transcription_calls,
        "clarifications": voice.clarifications,
        "passed": passed,
        "audio": audio_name,
        "audio_sha256": hashlib.sha256(_pcm(samples)).hexdigest(),
    }


async def run(args: argparse.Namespace) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output) if args.output else ROOT / "artifacts" / "stt-rejection" / stamp
    output.mkdir(parents=True, exist_ok=True)
    cases = [await _run_case(case, output) for case in await _corpus()]
    report = {
        "schema": "radcliffe-stt-rejection-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": args.commit,
        "synthetic_only": True,
        "passed": all(bool(case["passed"]) for case in cases),
        "cases": cases,
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(report_path)
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument(
        "--commit",
        default=subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
