import argparse
import hashlib
import importlib.util
import json
import stat
import sys
import wave
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "scripts" / "stt_capture_benchmark.py"
SPEC = importlib.util.spec_from_file_location("stt_capture_benchmark", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCHMARK
SPEC.loader.exec_module(BENCHMARK)


def write_capture(
    directory: Path,
    *,
    case_id: str,
    expected: str,
    coverage: list[str],
    route: str,
) -> None:
    pcm = b"\x01\x00" * 160
    wav_name = f"{case_id}.wav"
    with wave.open(str(directory / wav_name), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm)
    (directory / f"{case_id}.json").write_text(
        json.dumps(
            {
                "schema": "radcliffe-stt-capture-v1",
                "id": case_id,
                "expected": expected,
                "actual": expected,
                "required_keywords": [expected],
                "coverage": coverage,
                "route": route,
                "audio": wav_name,
                "audio_sha256": hashlib.sha256(pcm).hexdigest(),
                "transport": {"track_subscription_to_first_frame_ms": 2.0},
                "endpoint": {"pre_roll_ms": 600},
                "decoder": {"accepted": True, "reason": "accepted"},
            }
        ),
        encoding="utf-8",
    )


def test_private_benchmark_enforces_complete_coverage_and_private_report(
    monkeypatch,
    tmp_path,
) -> None:
    capture = tmp_path / "capture"
    capture.mkdir()
    write_capture(
        capture,
        case_id="speaker",
        expected="Speaker request",
        coverage=["first_turn", "multi_sentence", "short_command"],
        route="speaker",
    )
    write_capture(
        capture,
        case_id="earbud",
        expected="Earbud request",
        coverage=["natural_pause", "room_noise"],
        route="earbud",
    )

    expected_by_pcm = iter(("Earbud request", "Speaker request"))

    async def ordered_transcript(_provider, _pcm, _sample_rate, _channels) -> str:
        return next(expected_by_pcm)

    monkeypatch.setattr(BENCHMARK.LocalAppleSpeechProvider, "transcribe", ordered_transcript)
    output = tmp_path / "benchmark"

    result = BENCHMARK.run(
        argparse.Namespace(
            capture_directory=str(capture),
            profiles="large-v3-turbo-domain",
            output=str(output),
            require_pass=True,
            selected_profile="large-v3-turbo-domain",
        )
    )

    assert result == 0
    report_path = output / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"]
    assert report["selected_profile"] == "large-v3-turbo-domain"
    assert report["coverage"]["passed"]
    assert report["profiles"][0]["macro_word_error_rate"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
