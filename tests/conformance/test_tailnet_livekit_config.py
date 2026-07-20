from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NETWORK_LOADER = ROOT / "scripts" / "load-local-network-env.sh"


def run_network_loader(tmp_path: Path, *, mode: int) -> subprocess.CompletedProcess[str]:
    environment_file = tmp_path / "var" / "network.env"
    environment_file.parent.mkdir(parents=True)
    environment_file.write_text(
        "RADCLIFFE_LIVEKIT_URL=wss://tailnet.example:8089\n",
        encoding="utf-8",
    )
    environment_file.chmod(mode)
    environment = os.environ.copy()
    environment.pop("RADCLIFFE_LIVEKIT_URL", None)
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; set -a; source "$1"; set +a; printf "%s" "$RADCLIFFE_LIVEKIT_URL"',
            "bash",
            str(NETWORK_LOADER),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_network_override_is_exported_only_from_an_owner_only_file(tmp_path: Path) -> None:
    accepted = run_network_loader(tmp_path / "accepted", mode=0o600)
    rejected = run_network_loader(tmp_path / "rejected", mode=0o644)

    assert accepted.returncode == 0
    assert accepted.stdout == "wss://tailnet.example:8089"
    assert accepted.stderr == ""
    assert rejected.returncode != 0
    assert rejected.stdout == ""
    assert "mode 0600" in rejected.stderr


def test_local_livekit_advertises_configurable_tailnet_rtc_ports() -> None:
    compose = (ROOT / "infra" / "livekit" / "compose.yaml").read_text(encoding="utf-8")

    assert 'command: ["--dev"' not in compose
    assert "tcp_port: ${RADCLIFFE_LIVEKIT_RTC_PORT:-7881}" in compose
    assert '"--node-ip"' in compose
    assert '"--udp-port"' in compose
    assert "${RADCLIFFE_LIVEKIT_NODE_IP:-127.0.0.1}" in compose
    assert compose.count("${RADCLIFFE_LIVEKIT_RTC_PORT") == 6


def test_local_entrypoints_load_the_network_override() -> None:
    for runner_name in (
        "run-local-control.sh",
        "run-local-ops.sh",
        "run-local-worker.sh",
    ):
        runner = (ROOT / "scripts" / runner_name).read_text(encoding="utf-8")
        assert runner.index("source var/.env") < runner.index(
            "source scripts/load-local-network-env.sh"
        )
