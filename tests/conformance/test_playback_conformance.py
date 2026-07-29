import json
import subprocess
import sys


def test_every_bounded_playback_schedule_preserves_the_invariants(tmp_path) -> None:
    output = tmp_path / "playback-conformance.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/playback_conformance.py",
            "--output",
            str(output),
        ],
        check=True,
    )
    report = json.loads(output.read_text())

    assert report["scenario_count"] == 11
    assert report["maximum_observed_admitted_depth"] == 1
    assert report["maximum_observed_admitted_duration_ms"] <= 30_000
    media_first = report["scenarios"]["media_before_metadata"]
    assert media_first["trace"][1]["outcome"] == "blocked-until-metadata-accepted"
