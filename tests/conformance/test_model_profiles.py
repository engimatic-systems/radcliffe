from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE_RUNNER = ROOT / "scripts" / "run-sidecar-profile.sh"


def fake_node(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-node"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os

print(json.dumps({
    "provider": os.environ.get("RADCLIFFE_MODEL_PROVIDER"),
    "model_id": os.environ.get("RADCLIFFE_MODEL_ID"),
    "anthropic_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
    "protected_key_present": "RADCLIFFE_ANTHROPIC_API_KEY" in os.environ,
    "openai_key_present": bool(os.environ.get("OPENAI_API_KEY")),
    "bridge_matches_fake": os.environ.get("ANTHROPIC_API_KEY") == "fake-funded-key",
}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def environment_file(tmp_path: Path, node: Path, *, mode: int = 0o600) -> Path:
    configured = tmp_path / "radcliffe.env"
    configured.write_text(
        "\n".join(
            (
                f"RADCLIFFE_NODE_BIN={node}",
                "RADCLIFFE_ALLOW_CLOUD_MODELS=1",
                "RADCLIFFE_ANTHROPIC_API_KEY=fake-funded-key",
                "RADCLIFFE_OPENAI_API_KEY=fake-unused-key",
                "RADCLIFFE_MODEL_PROVIDER=untrusted-configured-provider",
                "RADCLIFFE_MODEL_ID=untrusted-configured-model",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    configured.chmod(mode)
    return configured


def run_profile(profile: str, configured: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["RADCLIFFE_ENV_FILE"] = str(configured)
    return subprocess.run(
        [str(PROFILE_RUNNER), profile],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_live_profile_pins_haiku_and_bridges_one_key_name(tmp_path: Path) -> None:
    result = run_profile(
        "live-haiku-4.5",
        environment_file(tmp_path, fake_node(tmp_path)),
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "anthropic_key_present": True,
        "protected_key_present": False,
        "openai_key_present": False,
        "bridge_matches_fake": True,
    }
    assert "conversation context is sent to Anthropic" in result.stderr
    assert "fake-funded-key" not in result.stderr


def test_local_profile_cannot_silently_inherit_cloud_configuration(tmp_path: Path) -> None:
    result = run_profile(
        "local-ollama",
        environment_file(tmp_path, fake_node(tmp_path)),
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["provider"] == "ollama"
    assert payload["model_id"] == "qwen3:8b"
    assert payload["anthropic_key_present"] is False
    assert payload["protected_key_present"] is False
    assert payload["openai_key_present"] is False


def test_profiles_reject_an_environment_file_that_is_not_owner_only(tmp_path: Path) -> None:
    configured = environment_file(tmp_path, fake_node(tmp_path), mode=0o644)

    result = run_profile("live-haiku-4.5", configured)

    assert result.returncode != 0
    assert "mode 0600" in result.stderr
    assert result.stdout == ""
