#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
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

    async def handle_transcript(self, transcript: str) -> None:
        self.transcripts.append(transcript)

    async def barge_in(self) -> None:
        return None


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

    cases: list[CorpusCase] = [
        CorpusCase("digital-silence", np.zeros(SAMPLE_RATE * 2, dtype=np.float32), False),
        CorpusCase("room-tone", room_tone, False),
        CorpusCase("post-aec-playback-echo", echo.astype(np.float32), False),
        CorpusCase("sparse-noise-short", sparse_short, False),
        CorpusCase("sparse-spikes-30s", sparse_maximum, False),
    ]
    for voice, phrase in (
        ("Samantha", "Go."),
        ("Daniel", "Stop."),
        ("Moira", "No."),
        ("Karen", "Yes."),
        ("Tessa", "Stop."),
        ("Fred", "Thank you."),
    ):
        provider = LocalAppleSpeechProvider(voice=voice)
        audio = await provider.synthesize(phrase)
        samples = provider._waveform(audio, 24000, 1)
        cases.append(
            CorpusCase(
                f"short-{voice.casefold()}-{_normalize(phrase).replace(' ', '-')}",
                samples,
                True,
                phrase,
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
        "transcript": transcript,
        "transcription_calls": speech.transcription_calls,
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
