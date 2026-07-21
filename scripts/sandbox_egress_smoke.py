#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from radcliffe_control.sandbox_agent import DockerSandbox


@dataclass(frozen=True, slots=True)
class SmokeCase:
    name: str
    command: str
    should_succeed: bool


CASES = (
    SmokeCase(
        "curl_https",
        "curl --fail --silent --show-error --max-time 20 https://example.com/ -o curl.html",
        True,
    ),
    SmokeCase(
        "wget_https",
        "wget --quiet --timeout=20 https://example.com/ -O wget.html",
        True,
    ),
    SmokeCase(
        "git_https",
        "git ls-remote https://github.com/octocat/Hello-World.git HEAD >/dev/null",
        True,
    ),
    SmokeCase(
        "python_https",
        'python -c "import urllib.request; '
        "urllib.request.urlopen('https://example.com/', timeout=20).read(32)\"",
        True,
    ),
    SmokeCase(
        "pip_download",
        "python -m pip download --disable-pip-version-check --no-deps "
        "--dest packages idna==3.10 >/dev/null",
        True,
    ),
    SmokeCase("npm_registry", "npm view is-number@7.0.0 version >/dev/null", True),
    SmokeCase(
        "no_direct_https",
        "env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy "
        "curl --noproxy '*' --fail --silent --max-time 5 "
        "https://example.com/ >/dev/null",
        False,
    ),
    SmokeCase("deny_rfc1918", "curl --fail --silent --max-time 5 http://10.0.0.1/", False),
    SmokeCase("deny_tailnet", "curl --fail --silent --max-time 5 http://100.64.0.1/", False),
    SmokeCase(
        "deny_metadata",
        "curl --fail --silent --max-time 5 http://169.254.169.254/",
        False,
    ),
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Exercise bounded sandbox public egress")
    value.add_argument("--docker", type=Path, required=True)
    value.add_argument("--docker-host", required=True)
    value.add_argument("--image", required=True)
    value.add_argument("--workspace-root", type=Path, required=True)
    return value


def resource_absent(sandbox: DockerSandbox, kind: str, name: str) -> bool:
    result = subprocess.run(
        [str(sandbox.docker), "-H", sandbox.docker_host, kind, "inspect", name],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    return result.returncode != 0


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    workspace_root = options.workspace_root.resolve()
    workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    cleanup_ok = False
    with tempfile.TemporaryDirectory(prefix="egress-smoke-", dir=workspace_root) as temporary:
        attempt = Path(temporary)
        workspace = attempt / "files"
        workspace.mkdir(mode=0o700)
        sandbox = DockerSandbox(
            docker=options.docker,
            docker_host=options.docker_host,
            image=options.image,
            workspace=workspace,
            workspace_root=workspace_root,
            job_id="sandbox-egress-smoke",
        )
        try:
            sandbox.start()
            for case in CASES:
                code, _output, truncated = sandbox.execute(case.command, timeout_seconds=30)
                passed = (code == 0) is case.should_succeed and not truncated
                results.append(
                    {
                        "case": case.name,
                        "status": "ok" if passed else "failed",
                        "exit_code": code,
                    }
                )
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            results.append(
                {
                    "case": "sandbox_start",
                    "status": "failed",
                    "error": type(error).__name__,
                }
            )
        finally:
            names = (sandbox.job_name, sandbox.proxy_name, sandbox.network_name)
            sandbox.cleanup()
            cleanup_ok = all(
                (
                    resource_absent(sandbox, "container", names[0]),
                    resource_absent(sandbox, "container", names[1]),
                    resource_absent(sandbox, "network", names[2]),
                )
            )
    checks_ok = len(results) == len(CASES) and all(result["status"] == "ok" for result in results)
    report = {
        "schema": "radcliffe-sandbox-egress-smoke-v1",
        "status": "ready" if checks_ok and cleanup_ok else "not_ready",
        "checks": results,
        "cleanup": "ok" if cleanup_ok else "failed",
        "limits": {
            "command_timeout_seconds": 30,
            "public_protocols": ["http", "https"],
        },
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
