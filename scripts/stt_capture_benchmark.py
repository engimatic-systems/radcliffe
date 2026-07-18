from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from radcliffe_media.speech import DEFAULT_WHISPER_INITIAL_PROMPT, LocalAppleSpeechProvider
from radcliffe_media.speech_capture import (
    CAPTURE_SCHEMA,
    REQUIRED_COVERAGE,
    REQUIRED_ROUTES,
    CaptureCase,
    corpus_coverage,
    dropped_leading_word_count,
)
from radcliffe_media.speech_quality import assess_transcript, summarize_quality


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: str
    model: str
    initial_prompt: str | None


PROFILES = {
    "large-v3-turbo": Profile(
        "large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo",
        None,
    ),
    "large-v3-turbo-domain": Profile(
        "large-v3-turbo-domain",
        "mlx-community/whisper-large-v3-turbo",
        DEFAULT_WHISPER_INITIAL_PROMPT,
    ),
    "large-v3": Profile(
        "large-v3",
        "mlx-community/whisper-large-v3-mlx",
        DEFAULT_WHISPER_INITIAL_PROMPT,
    ),
}
ROOT = Path(__file__).resolve().parent.parent


def _read_capture(path: Path) -> tuple[CaptureCase, dict[str, object], bytes, int, int]:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("schema") != CAPTURE_SCHEMA:
        raise ValueError(f"{path} is not a {CAPTURE_SCHEMA} record")
    case = CaptureCase.from_json(
        {
            "id": metadata.get("id"),
            "expected": metadata.get("expected"),
            "required_keywords": metadata.get("required_keywords"),
            "coverage": metadata.get("coverage"),
            "route": metadata.get("route"),
        }
    )
    audio_name = metadata.get("audio")
    if not isinstance(audio_name, str) or Path(audio_name).name != audio_name:
        raise ValueError(f"{path} contains an unsafe audio path")
    with wave.open(str(path.parent / audio_name), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError(f"{audio_name} must use signed 16-bit PCM")
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        pcm = source.readframes(source.getnframes())
    if metadata.get("audio_sha256") != hashlib.sha256(pcm).hexdigest():
        raise ValueError(f"{audio_name} does not match its captured PCM hash")
    return case, cast(dict[str, object], metadata), pcm, sample_rate, channels


async def _run_profile(
    profile: Profile,
    captures: list[tuple[CaptureCase, dict[str, object], bytes, int, int]],
) -> dict[str, object]:
    provider = LocalAppleSpeechProvider(
        whisper_model=profile.model,
        initial_prompt=profile.initial_prompt,
    )
    results = []
    qualities = []
    latencies = []
    for case, metadata, pcm, sample_rate, channels in captures:
        started = time.monotonic()
        transcript = await provider.transcribe(pcm, sample_rate, channels)
        latency = time.monotonic() - started
        quality = assess_transcript(
            case.expected,
            transcript,
            case.required_keywords,
            maximum_word_error_rate=1,
        )
        leading_drops = dropped_leading_word_count(case.expected, transcript)
        qualities.append(quality)
        latencies.append(latency)
        results.append(
            {
                "id": case.case_id,
                "audio": metadata["audio"],
                "audio_sha256": metadata["audio_sha256"],
                "hypothesis": transcript,
                "word_error_rate": round(quality.word_error_rate, 4),
                "keyword_recall": round(quality.keyword_recall, 4),
                "dropped_leading_words": leading_drops,
                "latency_seconds": round(latency, 3),
            }
        )
    summary = summarize_quality(qualities, latencies)
    maximum_leading_drops = max(cast(int, result["dropped_leading_words"]) for result in results)
    passed = (
        summary.macro_word_error_rate <= 0.03
        and summary.minimum_keyword_recall == 1.0
        and maximum_leading_drops == 0
    )
    return {
        "profile": asdict(profile),
        "passed": passed,
        "macro_word_error_rate": round(summary.macro_word_error_rate, 4),
        "minimum_keyword_recall": round(summary.minimum_keyword_recall, 4),
        "maximum_dropped_leading_words": maximum_leading_drops,
        "median_latency_seconds": round(summary.median_latency_seconds, 3),
        "maximum_latency_seconds": round(summary.maximum_latency_seconds, 3),
        "cases": results,
    }


def _diagnostics(
    captures: list[tuple[CaptureCase, dict[str, object], bytes, int, int]],
    profiles: list[dict[str, object]],
) -> list[dict[str, object]]:
    diagnostics = []
    for index, (case, metadata, _pcm, _sample_rate, _channels) in enumerate(captures):
        live_actual = str(metadata.get("actual", ""))
        live_drops = dropped_leading_word_count(case.expected, live_actual)
        profile_cases = [cast(list[dict[str, object]], item["cases"])[index] for item in profiles]
        decoded_drops = [cast(int, item["dropped_leading_words"]) for item in profile_cases]
        transport = cast(dict[str, object], metadata.get("transport", {}))
        if transport.get("track_subscription_to_first_frame_ms") is None:
            classification = "transport_loss"
        elif live_drops > 0 and decoded_drops and all(value > 0 for value in decoded_drops):
            classification = "endpoint_truncation"
        elif any(float(item["word_error_rate"]) < 0.03 for item in profile_cases):
            live_quality = assess_transcript(
                case.expected,
                live_actual,
                case.required_keywords,
                maximum_word_error_rate=1,
            )
            live_passed = (
                live_quality.word_error_rate <= 0.03
                and live_quality.keyword_recall == 1.0
                and live_drops == 0
            )
            classification = "no_error_detected" if live_passed else "decoder_error"
        else:
            classification = "decoder_error_or_acoustic_ambiguity"
        diagnostics.append(
            {
                "id": case.case_id,
                "classification": classification,
                "transport": metadata.get("transport"),
                "endpoint": metadata.get("endpoint"),
                "live_decoder": metadata.get("decoder"),
                "live_dropped_leading_words": live_drops,
            }
        )
    return diagnostics


def _write_private_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


def run(args: argparse.Namespace) -> int:
    capture_directory = Path(args.capture_directory).resolve()
    captures = [_read_capture(path) for path in sorted(capture_directory.glob("*.json"))]
    if not captures:
        raise ValueError("capture directory contains no completed capture records")
    cases = tuple(item[0] for item in captures)
    covered, routes = corpus_coverage(cases)
    coverage_passed = REQUIRED_COVERAGE <= covered and REQUIRED_ROUTES <= routes
    profile_names = tuple(PROFILES) if args.profiles == "all" else tuple(args.profiles.split(","))
    unknown = tuple(name for name in profile_names if name not in PROFILES)
    if unknown:
        raise ValueError(f"unknown profiles: {', '.join(unknown)}")
    if args.selected_profile not in profile_names:
        raise ValueError("selected profile must be included in the evaluated profiles")

    async def run_profiles() -> list[dict[str, object]]:
        return [await _run_profile(PROFILES[name], captures) for name in profile_names]

    reports = asyncio.run(run_profiles())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_directory = (
        Path(args.output).resolve() if args.output else capture_directory / "benchmarks" / stamp
    )
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    output_directory.chmod(0o700)
    selected = next(
        report
        for report in reports
        if cast(dict[str, object], report["profile"])["profile_id"] == args.selected_profile
    )
    passed = coverage_passed and bool(selected["passed"])
    report = {
        "schema": "radcliffe-private-stt-benchmark-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip(),
        "passed": passed,
        "selected_profile": args.selected_profile,
        "thresholds": {
            "maximum_macro_word_error_rate": 0.03,
            "minimum_exact_keyword_recall": 1.0,
            "maximum_dropped_leading_words": 0,
        },
        "coverage": {
            "passed": coverage_passed,
            "observed": sorted(covered),
            "required": sorted(REQUIRED_COVERAGE),
            "routes_observed": sorted(routes),
            "routes_required": sorted(REQUIRED_ROUTES),
        },
        "profiles": reports,
        "diagnostics": _diagnostics(captures, reports),
    }
    report_path = output_directory / "report.json"
    _write_private_json(report_path, report)
    print(
        json.dumps(
            {
                "report": str(report_path),
                "passed": passed,
                "coverage_passed": coverage_passed,
                "selected_profile": args.selected_profile,
                "profiles": [
                    {
                        "id": item["profile"]["profile_id"],
                        "passed": item["passed"],
                        "macro_word_error_rate": item["macro_word_error_rate"],
                        "minimum_keyword_recall": item["minimum_keyword_recall"],
                        "maximum_dropped_leading_words": item["maximum_dropped_leading_words"],
                    }
                    for item in reports
                ],
            },
            indent=2,
        )
    )
    return 1 if args.require_pass and not passed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark local decoders against opt-in private Pixel PCM",
    )
    parser.add_argument("capture_directory")
    parser.add_argument(
        "--profiles",
        default="all",
        help="all or comma-separated large-v3-turbo,large-v3-turbo-domain,large-v3",
    )
    parser.add_argument("--output")
    parser.add_argument(
        "--selected-profile",
        default="large-v3-turbo-domain",
        choices=tuple(PROFILES),
    )
    parser.add_argument("--require-pass", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
