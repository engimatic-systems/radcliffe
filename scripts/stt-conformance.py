#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from radcliffe_media.speech import DEFAULT_WHISPER_INITIAL_PROMPT, LocalAppleSpeechProvider
from radcliffe_media.speech_quality import assess_transcript, summarize_quality


@dataclass(frozen=True, slots=True)
class CorpusCase:
    case_id: str
    segments: tuple[str, ...]
    voice: str
    required_keywords: tuple[str, ...]
    pause_ms: int = 0
    noise_snr_db: float | None = None
    echo_delay_ms: int = 0
    echo_gain: float = 0
    frame_dropout_rate: float = 0
    transcript_reference: str | None = None

    @property
    def spoken_text(self) -> str:
        return " ".join(self.segments)

    @property
    def reference(self) -> str:
        return self.transcript_reference or self.spoken_text


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    model: str
    initial_prompt: str | None = None


CASES = (
    CorpusCase(
        "ack-ok-us-male-clean",
        ("OK.",),
        "Fred",
        ("okay",),
        transcript_reference="Okay.",
    ),
    CorpusCase(
        "ack-ok-us-male-phone-like",
        ("OK.",),
        "Reed (English (US))",
        ("okay",),
        noise_snr_db=8,
        echo_delay_ms=70,
        echo_gain=0.25,
        frame_dropout_rate=0.025,
        transcript_reference="Okay.",
    ),
    CorpusCase(
        "ack-ok-count-us-male-phone-like",
        ("OK, count from one to fifty.",),
        "Rocko (English (US))",
        ("okay", "count", "50"),
        noise_snr_db=8,
        echo_delay_ms=70,
        echo_gain=0.25,
        frame_dropout_rate=0.025,
        transcript_reference="Okay, count from 1 to 50.",
    ),
    CorpusCase(
        "ack-ok-stop-us-male",
        ("OK, stop and switch topic.",),
        "Eddy (English (US))",
        ("okay", "stop", "switch topic"),
        transcript_reference="Okay, stop and switch topic.",
    ),
    CorpusCase(
        "cosmic-command-us",
        (
            "Please start your response exactly with dash, bold Cosmic touch, "
            "then give me a moon fact.",
        ),
        "Samantha",
        ("dash", "bold", "Cosmic touch", "moon"),
    ),
    CorpusCase(
        "cosmic-command-uk",
        (
            "Please start your response exactly with dash, bold Cosmic touch, "
            "then give me a moon fact.",
        ),
        "Daniel",
        ("dash", "bold", "Cosmic touch", "moon"),
    ),
    CorpusCase(
        "cosmic-command-natural-pause",
        (
            "Please start your response exactly with dash,",
            "bold Cosmic touch, then give me a moon fact.",
        ),
        "Moira",
        ("dash", "bold", "Cosmic touch", "moon"),
        pause_ms=450,
    ),
    CorpusCase(
        "cosmic-command-phone-like",
        (
            "Please start your response exactly with dash, bold Cosmic touch, "
            "then give me a moon fact.",
        ),
        "Samantha",
        ("dash", "bold", "Cosmic touch", "moon"),
        noise_snr_db=9,
        echo_delay_ms=85,
        echo_gain=0.3,
        frame_dropout_rate=0.025,
    ),
    CorpusCase(
        "voice-controls-noise",
        ("Reply with exactly voice controls work.",),
        "Samantha",
        ("reply", "voice controls work"),
        noise_snr_db=8,
        echo_delay_ms=70,
        echo_gain=0.25,
    ),
    CorpusCase(
        "source-reading-command",
        (
            "Open the file called lunar field notes and read it out loud, "
            "word for word, but skip the metadata.",
        ),
        "Tessa",
        ("lunar field notes", "word for word", "metadata"),
    ),
    CorpusCase(
        "source-reading-fixture-command",
        ("Use the read source tool to read radcliffe dash demo dot M D word for word.",),
        "Karen",
        ("read source", "radcliffe", "demo", "word for word"),
        transcript_reference=("Use the read source tool to read radcliffe-demo.md word for word."),
    ),
    CorpusCase(
        "multi-clause-natural-pause",
        (
            "Tell me a short joke,",
            "then let us move on to a different topic.",
        ),
        "Fred",
        ("joke", "different topic"),
        pause_ms=600,
    ),
    CorpusCase(
        "multi-clause-long-pause",
        (
            "Tell me a dirty joke.",
            "Actually, ignore that and reply with exactly changed subject.",
        ),
        "Fred",
        ("dirty joke", "ignore", "changed subject"),
        pause_ms=1200,
    ),
)

PROFILES = {
    "large-v3-turbo": Profile(
        "large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo",
    ),
    "large-v3-turbo-domain": Profile(
        "large-v3-turbo-domain",
        "mlx-community/whisper-large-v3-turbo",
        DEFAULT_WHISPER_INITIAL_PROMPT,
    ),
    "large-v3": Profile(
        "large-v3",
        "mlx-community/whisper-large-v3-mlx",
    ),
}
ROOT = Path(__file__).resolve().parent.parent


def _pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm)


def _degrade(samples: np.ndarray, case: CorpusCase) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(case.case_id.encode()).digest()[:8])
    random = np.random.default_rng(seed)
    degraded = samples.copy()
    if case.echo_delay_ms:
        delay = round(case.echo_delay_ms * 16)
        echoed = np.zeros_like(degraded)
        echoed[delay:] = degraded[:-delay] * case.echo_gain
        degraded += echoed
    if case.frame_dropout_rate:
        frames = len(degraded) // 320
        dropped = random.random(frames) < case.frame_dropout_rate
        for index in np.flatnonzero(dropped):
            degraded[index * 320 : (index + 1) * 320] = 0
    if case.noise_snr_db is None:
        return np.clip(degraded, -1, 1)
    active = samples[np.abs(samples) > 0.001]
    signal_rms = float(np.sqrt(np.mean(active**2))) if active.size else 0.01
    noise_rms = signal_rms / (10 ** (case.noise_snr_db / 20))
    noise = random.normal(0, noise_rms, len(degraded)).astype(np.float32)
    return np.clip(degraded + noise, -1, 1)


async def _render_case(case: CorpusCase) -> np.ndarray:
    provider = LocalAppleSpeechProvider(voice=case.voice)
    rendered: list[np.ndarray] = []
    for index, text in enumerate(case.segments):
        audio = await provider.synthesize(text)
        rendered.append(provider._waveform(audio, 24000, 1))
        if index < len(case.segments) - 1:
            rendered.append(np.zeros(round(case.pause_ms * 16), dtype=np.float32))
    samples = np.concatenate(rendered)
    samples = _degrade(samples, case)
    return np.concatenate(
        [
            np.zeros(3200, dtype=np.float32),
            samples,
            np.zeros(8000, dtype=np.float32),
        ]
    )


async def _run_profile(
    profile: Profile,
    corpus: dict[str, np.ndarray],
) -> dict[str, object]:
    provider = LocalAppleSpeechProvider(
        whisper_model=profile.model,
        initial_prompt=profile.initial_prompt,
    )
    cases: list[dict[str, object]] = []
    quality_results = []
    latencies = []
    for case in CASES:
        samples = corpus[case.case_id]
        started = time.monotonic()
        result = await provider.transcribe_result(_pcm(samples), 16000, 1)
        latency = time.monotonic() - started
        hypothesis = str(result["text"]).strip()
        quality = assess_transcript(case.reference, hypothesis, case.required_keywords)
        quality_results.append(quality)
        latencies.append(latency)
        model_segments = result.get("segments", [])
        cases.append(
            {
                "id": case.case_id,
                "reference": case.reference,
                "hypothesis": hypothesis,
                "required_keywords": list(case.required_keywords),
                "word_error_rate": round(quality.word_error_rate, 4),
                "keyword_recall": round(quality.keyword_recall, 4),
                "model_segment_count": len(model_segments)
                if isinstance(model_segments, list)
                else None,
                "latency_seconds": round(latency, 3),
                "passed": quality.passed,
            }
        )
    summary = summarize_quality(quality_results, latencies)
    return {
        "profile": asdict(profile),
        "passed": summary.passed,
        "macro_word_error_rate": round(summary.macro_word_error_rate, 4),
        "minimum_keyword_recall": summary.minimum_keyword_recall,
        "median_latency_seconds": round(summary.median_latency_seconds, 3),
        "maximum_latency_seconds": round(summary.maximum_latency_seconds, 3),
        "cases": cases,
    }


def _selected_profiles(value: str) -> tuple[Profile, ...]:
    names = tuple(PROFILES) if value == "all" else tuple(value.split(","))
    unknown = tuple(name for name in names if name not in PROFILES)
    if unknown:
        raise ValueError(f"unknown profiles: {', '.join(unknown)}")
    return tuple(PROFILES[name] for name in names)


async def run(args: argparse.Namespace) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output) if args.output else ROOT / "artifacts" / "stt-conformance" / stamp
    output.mkdir(parents=True, exist_ok=True)
    corpus: dict[str, np.ndarray] = {}
    corpus_manifest: list[dict[str, object]] = []
    for case in CASES:
        samples = await _render_case(case)
        corpus[case.case_id] = samples
        wav_name = f"{case.case_id}.wav"
        _write_wav(output / wav_name, _pcm(samples))
        corpus_manifest.append(
            {
                "id": case.case_id,
                "voice": case.voice,
                "spoken_text": case.spoken_text,
                "reference": case.reference,
                "required_keywords": list(case.required_keywords),
                "pause_ms": case.pause_ms,
                "noise_snr_db": case.noise_snr_db,
                "echo_delay_ms": case.echo_delay_ms,
                "echo_gain": case.echo_gain,
                "frame_dropout_rate": case.frame_dropout_rate,
                "audio": wav_name,
                "audio_sha256": hashlib.sha256(_pcm(samples)).hexdigest(),
            }
        )
    reports = []
    for profile in _selected_profiles(args.profiles):
        reports.append(await _run_profile(profile, corpus))
    report = {
        "schema": "radcliffe-stt-conformance-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": args.commit,
        "seed_policy": "sha256(case-id) for deterministic Gaussian noise",
        "thresholds": {
            "maximum_case_word_error_rate": 0.2,
            "minimum_case_keyword_recall": 1.0,
        },
        "corpus": corpus_manifest,
        "profiles": reports,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    summary = {
        "report": str(output / "report.json"),
        "profiles": [
            {
                "id": item["profile"]["profile_id"],
                "passed": item["passed"],
                "macro_word_error_rate": item["macro_word_error_rate"],
                "minimum_keyword_recall": item["minimum_keyword_recall"],
                "median_latency_seconds": item["median_latency_seconds"],
            }
            for item in reports
        ],
    }
    print(json.dumps(summary, indent=2))
    return 1 if args.require_pass and not all(bool(item["passed"]) for item in reports) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Radcliffe STT acoustic corpus")
    parser.add_argument(
        "--profiles",
        default="all",
        help=("all or a comma-separated subset of large-v3-turbo,large-v3-turbo-domain,large-v3"),
    )
    parser.add_argument("--output", help="artifact directory")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    args.commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
