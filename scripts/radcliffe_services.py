#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    name: str
    label: str
    runner: str
    log: str


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    name: str
    label: str
    installed: bool
    loaded: bool
    state: str
    pid: int | None
    last_exit_code: int | None


SERVICES = (
    ServiceDefinition(
        "sidecar",
        "works.ravd.radcliffe.sidecar",
        "scripts/run-local-sidecar.sh",
        "var/agent-sidecar.log",
    ),
    ServiceDefinition(
        "control",
        "works.ravd.radcliffe.control",
        "scripts/run-local-control.sh",
        "var/control-plane.log",
    ),
    ServiceDefinition(
        "compat-worker",
        "works.ravd.radcliffe.worker",
        "scripts/run-local-worker.sh",
        "var/media-worker.log",
    ),
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def selected_services(include_compat_worker: bool) -> tuple[ServiceDefinition, ...]:
    return SERVICES if include_compat_worker else SERVICES[:2]


def build_plist(root: Path, service: ServiceDefinition) -> dict[str, Any]:
    runner = root / service.runner
    log = root / service.log
    return {
        "Label": service.label,
        "ProgramArguments": [str(runner)],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def validate_workspace(root: Path) -> None:
    if not root.is_absolute() or not (root / "pyproject.toml").is_file():
        raise RuntimeError("Radcliffe workspace root is invalid")
    environment = root / "var/.env"
    if not environment.is_file():
        raise RuntimeError("protected var/.env is required before installing services")
    if environment.stat().st_mode & 0o077:
        raise RuntimeError("protected var/.env must not be accessible by group or others")
    for service in SERVICES:
        runner = root / service.runner
        if not runner.is_file() or not os.access(runner, os.X_OK):
            raise RuntimeError(f"service runner is missing or not executable: {service.name}")


def write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ServiceManager:
    def __init__(
        self,
        *,
        root: Path,
        launch_agents: Path,
        uid: int,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        self.root = root.resolve()
        self.launch_agents = launch_agents
        self.domain = f"gui/{uid}"
        self.command_runner = command_runner

    def install(self, services: Sequence[ServiceDefinition]) -> None:
        validate_workspace(self.root)
        for service in services:
            (self.root / service.log).parent.mkdir(parents=True, exist_ok=True)
            path = self._plist_path(service)
            write_plist(path, build_plist(self.root, service))
        self._reload(services)

    def uninstall(self, services: Sequence[ServiceDefinition]) -> None:
        for service in reversed(services):
            self._bootout(service)
        for service in services:
            self._plist_path(service).unlink(missing_ok=True)

    def start(self, services: Sequence[ServiceDefinition]) -> None:
        for service in services:
            path = self._plist_path(service)
            if not path.is_file():
                raise RuntimeError(f"service is not installed: {service.name}")
            status = self._run(
                ("print", f"{self.domain}/{service.label}"),
                check=False,
            )
            if status.returncode != 0:
                self._run(("bootstrap", self.domain, str(path)))
            elif _match_text(status.stdout, r"^\s*state = ([A-Za-z]+)$") != "running":
                self._run(("kickstart", f"{self.domain}/{service.label}"))

    def stop(self, services: Sequence[ServiceDefinition]) -> None:
        for service in reversed(services):
            self._bootout(service)

    def restart(self, services: Sequence[ServiceDefinition]) -> None:
        self.stop(services)
        self.start(services)

    def statuses(self, services: Sequence[ServiceDefinition]) -> tuple[ServiceStatus, ...]:
        results: list[ServiceStatus] = []
        for service in services:
            path = self._plist_path(service)
            command = self._run(("print", f"{self.domain}/{service.label}"), check=False)
            output = command.stdout if command.returncode == 0 else ""
            results.append(
                ServiceStatus(
                    name=service.name,
                    label=service.label,
                    installed=path.is_file(),
                    loaded=command.returncode == 0,
                    state=_match_text(output, r"^\s*state = ([A-Za-z]+)$") or "unloaded",
                    pid=_match_int(output, r"^\s*pid = (\d+)$"),
                    last_exit_code=_match_int(output, r"^\s*last exit code = (-?\d+)$"),
                )
            )
        return tuple(results)

    def _reload(self, services: Sequence[ServiceDefinition]) -> None:
        for service in reversed(services):
            self._bootout(service)
        self.start(services)

    def _bootout(self, service: ServiceDefinition) -> None:
        status = self._run(("print", f"{self.domain}/{service.label}"), check=False)
        pid = _match_int(status.stdout, r"^\s*pid = (\d+)$") if status.returncode == 0 else None
        self._run(("bootout", f"{self.domain}/{service.label}"), check=False)
        if pid is None:
            return
        deadline = time.monotonic() + 5
        while process_is_alive(pid):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"service did not stop within 5 seconds: {service.name}")
            time.sleep(0.05)

    def _plist_path(self, service: ServiceDefinition) -> Path:
        return self.launch_agents / f"{service.label}.plist"

    def _run(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.command_runner(
            ("/bin/launchctl", *arguments),
            check=check,
            capture_output=True,
            text=True,
        )


def _match_text(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value, re.MULTILINE)
    return match.group(1) if match else None


def _match_int(value: str, pattern: str) -> int | None:
    matched = _match_text(value, pattern)
    return int(matched) if matched is not None else None


def render_status(statuses: Sequence[ServiceStatus], as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "schema": "radcliffe-local-services-v1",
                "services": [asdict(status) for status in statuses],
            },
            indent=2,
        )
    return "\n".join(
        f"{status.name:14} {status.state:9} "
        f"installed={'yes' if status.installed else 'no'} "
        f"pid={status.pid if status.pid is not None else '-'}"
        for status in statuses
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage Radcliffe local macOS services")
    result.add_argument(
        "command",
        choices=("install", "uninstall", "start", "stop", "restart", "status"),
    )
    result.add_argument(
        "--include-compat-worker",
        action="store_true",
        help="also manage the legacy fixed-room compatibility worker",
    )
    result.add_argument("--json", action="store_true", help="emit machine-readable status")
    result.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    result.add_argument(
        "--launch-agents",
        type=Path,
        default=Path.home() / "Library/LaunchAgents",
        help=argparse.SUPPRESS,
    )
    return result


def main() -> int:
    arguments = parser().parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("Radcliffe LaunchAgent management requires macOS")
    services = selected_services(arguments.include_compat_worker)
    manager = ServiceManager(
        root=arguments.root,
        launch_agents=arguments.launch_agents,
        uid=os.getuid(),
    )
    if arguments.command == "status":
        print(render_status(manager.statuses(services), arguments.json))
        return 0
    getattr(manager, arguments.command)(services)
    print(f"{arguments.command} completed for {', '.join(service.name for service in services)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
