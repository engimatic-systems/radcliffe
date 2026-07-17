import json
import os
import plistlib
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts/radcliffe_services.py"
SPEC = spec_from_file_location("radcliffe_services_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVICES_MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVICES_MODULE
SPEC.loader.exec_module(SERVICES_MODULE)

SERVICES = SERVICES_MODULE.SERVICES
ServiceManager = SERVICES_MODULE.ServiceManager
build_plist = SERVICES_MODULE.build_plist
render_status = SERVICES_MODULE.render_status
selected_services = SERVICES_MODULE.selected_services
validate_workspace = SERVICES_MODULE.validate_workspace


class FakeLaunchctl:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, **_options) -> subprocess.CompletedProcess[str]:
        values = tuple(command)
        self.commands.append(values)
        action = values[1]
        if action == "bootout":
            self.loaded.discard(values[2].rsplit("/", 1)[-1])
            return subprocess.CompletedProcess(values, 0, "", "")
        if action == "bootstrap":
            with Path(values[3]).open("rb") as stream:
                label = str(plistlib.load(stream)["Label"])
            self.loaded.add(label)
            return subprocess.CompletedProcess(values, 0, "", "")
        if action == "print":
            label = values[2].rsplit("/", 1)[-1]
            if label not in self.loaded:
                return subprocess.CompletedProcess(values, 113, "", "not found")
            return subprocess.CompletedProcess(
                values,
                0,
                "state = running\npid = 4242\nlast exit code = 0\n",
                "",
            )
        if action == "kickstart":
            return subprocess.CompletedProcess(values, 0, "", "")
        raise AssertionError(f"unexpected launchctl action {action}")


def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "radcliffe"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n")
    environment = root / "var/.env"
    environment.parent.mkdir()
    environment.write_text("RADCLIFFE_INTERNAL_TOKEN=not-copied-to-plist\n")
    environment.chmod(0o600)
    for service in SERVICES:
        runner = root / service.runner
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text("#!/bin/sh\nexit 0\n")
        runner.chmod(0o755)
    return root


def test_plist_is_repository_owned_and_contains_no_environment_or_secret(tmp_path) -> None:
    root = workspace(tmp_path)
    payload = build_plist(root, SERVICES[0])
    encoded = plistlib.dumps(payload)

    assert payload["ProgramArguments"] == [str(root / SERVICES[0].runner)]
    assert payload["WorkingDirectory"] == str(root)
    assert payload["Umask"] == 0o077
    assert "EnvironmentVariables" not in payload
    assert b"not-copied-to-plist" not in encoded
    assert b"TOKEN" not in encoded


def test_workspace_validation_requires_private_environment_and_executable_runners(
    tmp_path,
) -> None:
    root = workspace(tmp_path)
    validate_workspace(root)

    environment = root / "var/.env"
    environment.chmod(0o644)
    with pytest.raises(RuntimeError, match="group or others"):
        validate_workspace(root)
    environment.chmod(0o600)

    runner = root / SERVICES[0].runner
    runner.chmod(0o644)
    with pytest.raises(RuntimeError, match="not executable"):
        validate_workspace(root)


def test_install_status_restart_and_uninstall_are_idempotent(tmp_path) -> None:
    root = workspace(tmp_path)
    launch_agents = tmp_path / "LaunchAgents"
    launchctl = FakeLaunchctl()
    manager = ServiceManager(
        root=root,
        launch_agents=launch_agents,
        uid=501,
        command_runner=launchctl,
    )
    services = selected_services(include_compat_worker=True)

    manager.install(services)
    manager.install(services)
    statuses = manager.statuses(services)
    manager.restart(services)
    manager.uninstall(services)
    manager.uninstall(services)

    assert all(status.installed and status.loaded for status in statuses)
    assert {status.state for status in statuses} == {"running"}
    assert {status.pid for status in statuses} == {4242}
    assert not tuple(launch_agents.glob("*.plist"))
    assert not launchctl.loaded

    machine_status = json.loads(render_status(statuses, as_json=True))
    assert machine_status["schema"] == "radcliffe-local-services-v1"
    assert {item["name"] for item in machine_status["services"]} == {
        "sidecar",
        "control",
        "compat-worker",
    }
    assert str(root / "var/.env") not in json.dumps(machine_status)


def test_generated_plist_is_owner_only(tmp_path) -> None:
    root = workspace(tmp_path)
    launch_agents = tmp_path / "LaunchAgents"
    manager = ServiceManager(
        root=root,
        launch_agents=launch_agents,
        uid=os.getuid(),
        command_runner=FakeLaunchctl(),
    )

    manager.install(selected_services(include_compat_worker=False))

    for path in launch_agents.glob("*.plist"):
        assert path.stat().st_mode & 0o777 == 0o600
