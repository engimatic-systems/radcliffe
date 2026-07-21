import json
import subprocess
from pathlib import Path

import pytest
from radcliffe_control.job_results import read_promoted_result
from radcliffe_control.sandbox_agent import (
    COMMAND_OUTPUT_LIMIT,
    HAIKU_MODEL_ID,
    SYSTEM_PROMPT,
    AnthropicAgent,
    DockerSandbox,
    OllamaAgent,
    PrivateTranscript,
    StartFrame,
    run_agent,
    safe_artifacts,
)


def test_background_prompt_keeps_radcliffe_as_the_harness_not_the_model() -> None:
    normalized = " ".join(SYSTEM_PROMPT.split())
    assert normalized.count("harness is called Radcliffe") == 1
    assert "selected Unix job model" in normalized
    assert "set inference true" in normalized
    assert "paths of every source" in normalized
    assert "You are Radcliffe" not in normalized
    assert "Radcliffe's background Unix job agent" not in normalized


def start_frame() -> StartFrame:
    return StartFrame.model_validate(
        {
            "type": "start",
            "schemaVersion": 1,
            "jobId": "job-1",
            "attempt": 1,
            "instruction": "Create and check a report.",
            "followUps": [],
            "limits": {
                "budgetUsd": None,
                "maxRuntimeSeconds": 300,
                "maxOutputBytes": 65_536,
            },
        }
    )


class FakeSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.commands: list[tuple[str, int]] = []

    def execute(self, command: str, *, timeout_seconds: int) -> tuple[int, str, bool]:
        self.commands.append((command, timeout_seconds))
        self.workspace.joinpath("report.md").write_text("verified result\n")
        return 0, "private shell output", False


class ScriptedOllama(OllamaAgent):
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages
        self.contexts: list[list[dict[str, object]]] = []

    def chat(self, messages: list[dict[str, object]]) -> dict[str, object]:
        self.contexts.append(json.loads(json.dumps(messages)))
        return self.messages.pop(0)


def tool_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def tool_batch(*calls: tuple[str, dict[str, object]]) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"call-{index}",
                "function": {"name": name, "arguments": arguments},
            }
            for index, (name, arguments) in enumerate(calls)
        ],
    }


def test_command_output_limit_leaves_room_for_the_bounded_model_context() -> None:
    assert COMMAND_OUTPUT_LIMIT == 24 * 1024


def test_ollama_request_bounds_context_and_generation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"message":{"role":"assistant","content":"bounded"}}'

    def open_request(request, *, timeout):
        captured.update(json.loads(request.data))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_request)

    message = OllamaAgent(url="http://127.0.0.1:11434", model="qwen3:8b").chat([])

    assert message["content"] == "bounded"
    assert captured["options"] == {
        "temperature": 0.1,
        "num_ctx": 8_192,
        "num_predict": 512,
    }


def test_anthropic_job_model_is_pinned_bounded_and_forces_one_tool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "run_shell",
                            "input": {"command": "true", "purpose": "testing"},
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            ).encode()

    def open_request(request, *, timeout):
        captured.update(json.loads(request.data))
        captured["timeout"] = timeout
        captured["api_key"] = request.get_header("X-api-key")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    agent = AnthropicAgent(api_key="fake-key", budget_usd=0.25)

    message = agent.chat(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "do work"},
        ]
    )

    assert captured["model"] == HAIKU_MODEL_ID
    assert captured["max_tokens"] == 1024
    assert captured["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    assert captured["api_key"] == "fake-key"
    assert message["tool_calls"] == [
        {
            "id": "toolu_1",
            "function": {
                "name": "run_shell",
                "arguments": {"command": "true", "purpose": "testing"},
            },
        }
    ]
    assert agent.spent_usd == pytest.approx(0.0002)


def test_agent_promotes_only_bounded_result_and_artifacts(tmp_path, capsys) -> None:
    attempt_workspace = tmp_path / "jobs" / "job-1" / "attempt-1"
    artifact_workspace = attempt_workspace / "files"
    artifact_workspace.mkdir(parents=True)
    sandbox = FakeSandbox(artifact_workspace)
    ollama = ScriptedOllama(
        [
            tool_call(
                "run_shell",
                {"command": "python verify.py", "purpose": "testing", "timeoutSeconds": 12},
            ),
            tool_call(
                "finish_job",
                {
                    "summary": "Verified the result.",
                    "artifactPaths": ["report.md"],
                    "sources": [
                        {
                            "name": "A Verified Result",
                            "locator": "https://example.test/paper",
                            "path": "report.md",
                        }
                    ],
                    "findings": [
                        {
                            "text": "The result was verified.",
                            "sourcePaths": ["report.md"],
                            "inference": False,
                        }
                    ],
                },
            ),
        ]
    )
    transcript = PrivateTranscript(attempt_workspace)
    try:
        assert run_agent(
            start_frame(),
            sandbox=sandbox,  # type: ignore[arg-type]
            agent=ollama,
            transcript=transcript,
            max_steps=3,
            result_workspace=attempt_workspace,
        )
    finally:
        transcript.close()

    emitted = capsys.readouterr().out
    frames = [json.loads(line) for line in emitted.splitlines()]
    assert [frame["kind"] for frame in frames] == ["progress", "succeeded"]
    assert "private shell output" not in emitted
    assert "python verify.py" not in emitted
    promoted = read_promoted_result(tmp_path / "jobs", job_id="job-1", attempt=1)
    assert promoted is not None
    assert promoted.summary == "Verified the result."
    assert promoted.artifacts[0].path == "report.md"
    assert promoted.sources[0].name == "A Verified Result"
    assert promoted.sources[0].sha256 == promoted.artifacts[0].sha256
    assert promoted.findings[0].source_entity_ids == (promoted.sources[0].entity_id,)
    assert not promoted.findings[0].inference
    assert frames[-1]["entityIds"] == [
        promoted.sources[0].entity_id,
        promoted.findings[0].entity_id,
    ]
    assert not artifact_workspace.joinpath("agent-transcript.jsonl").exists()
    assert not artifact_workspace.joinpath("promoted-result.json").exists()
    assert sandbox.commands == [("python verify.py", 12)]


def test_agent_fails_boundedly_when_model_never_uses_a_tool(tmp_path, capsys) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    transcript = PrivateTranscript(workspace)
    try:
        assert not run_agent(
            start_frame(),
            sandbox=FakeSandbox(workspace),  # type: ignore[arg-type]
            agent=ScriptedOllama([{"role": "assistant", "content": "no tool"}]),
            transcript=transcript,
            max_steps=1,
        )
    finally:
        transcript.close()
    assert json.loads(capsys.readouterr().out)["detailCode"] == "step_limit_exhausted"


def test_agent_fails_semantically_unsuccessful_work_with_a_promoted_reason(
    tmp_path,
    capsys,
) -> None:
    attempt_workspace = tmp_path / "jobs" / "job-1" / "attempt-1"
    artifact_workspace = attempt_workspace / "files"
    artifact_workspace.mkdir(parents=True)
    sandbox = FakeSandbox(artifact_workspace)
    agent = ScriptedOllama(
        [
            tool_call(
                "run_shell",
                {"command": "curl blocked.example", "purpose": "downloading"},
            ),
            tool_call(
                "fail_job",
                {
                    "summary": (
                        "The requested source could not be acquired after a grounded attempt."
                    ),
                    "reasonCode": "source_unavailable",
                },
            ),
        ]
    )
    transcript = PrivateTranscript(attempt_workspace)
    try:
        assert not run_agent(
            start_frame(),
            sandbox=sandbox,  # type: ignore[arg-type]
            agent=agent,
            transcript=transcript,
            max_steps=2,
            result_workspace=attempt_workspace,
        )
    finally:
        transcript.close()

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(frame["kind"], frame["detailCode"]) for frame in frames] == [
        ("progress", "downloading"),
        ("failed", "source_unavailable"),
    ]
    result = read_promoted_result(tmp_path / "jobs", job_id="job-1", attempt=1)
    assert result is not None
    assert "could not be acquired" in result.summary


def test_agent_does_not_execute_an_identical_failed_command_twice(tmp_path, capsys) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()

    class FailedSandbox(FakeSandbox):
        def execute(self, command: str, *, timeout_seconds: int) -> tuple[int, str, bool]:
            self.commands.append((command, timeout_seconds))
            return 1, "failed", False

    sandbox = FailedSandbox(workspace)
    repeated = tool_call(
        "run_shell",
        {"command": "curl bad.example", "purpose": "downloading"},
    )
    agent = ScriptedOllama(
        [
            repeated,
            repeated,
            tool_call(
                "fail_job",
                {"summary": "The source stayed unavailable.", "reasonCode": "source_unavailable"},
            ),
        ]
    )
    transcript = PrivateTranscript(workspace)
    try:
        assert not run_agent(
            start_frame(),
            sandbox=sandbox,  # type: ignore[arg-type]
            agent=agent,
            transcript=transcript,
            max_steps=3,
        )
    finally:
        transcript.close()

    assert sandbox.commands == [("curl bad.example", 60)]
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["detailCode"] == (
        "source_unavailable"
    )


def test_agent_sequences_and_repairs_the_observed_batched_fibonacci_calls(tmp_path, capsys) -> None:
    attempt_workspace = tmp_path / "jobs" / "job-1" / "attempt-1"
    artifact_workspace = attempt_workspace / "files"
    artifact_workspace.mkdir(parents=True)

    class FibonacciSandbox(FakeSandbox):
        def execute(self, command: str, *, timeout_seconds: int) -> tuple[int, str, bool]:
            self.commands.append((command, timeout_seconds))
            if "fibonacci_script.py" in command and ">" in command:
                self.workspace.joinpath("fibonacci_script.py").write_text(
                    "print(sum([1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]) / 17)\n"
                )
                return 0, "", False
            if command == "python3 fibonacci_script.py":
                return 0, "13.647058823529411\n", False
            return 1, "unexpected command", False

    sandbox = FibonacciSandbox(artifact_workspace)
    ollama = ScriptedOllama(
        [
            tool_batch(
                (
                    "run_shell",
                    {
                        "command": "printf script > fibonacci_script.py",
                        "purpose": "coding",
                        "timeoutSeconds": 30,
                    },
                ),
                (
                    "run_shell",
                    {
                        "command": "python3 fibonacci_script.py",
                        "purpose": "executing",
                        "timeoutSeconds": 10,
                    },
                ),
                (
                    "finish_job",
                    {
                        "artifactPaths": ["/workspace/fibonacci_script.py"],
                        "summary": "Calculated and checked the result.",
                    },
                ),
            ),
            tool_batch(
                (
                    "run_shell",
                    {
                        "command": "python3 fibonacci_script.py",
                        "purpose": "executing",
                        "timeoutSeconds": 10,
                    },
                ),
                (
                    "finish_job",
                    {
                        "artifactPaths": ["/workspace/fibonacci_script.py"],
                        "summary": "Calculated and checked the result.",
                    },
                ),
            ),
            tool_call(
                "finish_job",
                {
                    "artifactPaths": ["/workspace/fibonacci_script.py"],
                    "summary": "Calculated and checked the result: 13.647058823529411.",
                },
            ),
        ]
    )
    transcript = PrivateTranscript(attempt_workspace)
    try:
        assert run_agent(
            start_frame(),
            sandbox=sandbox,  # type: ignore[arg-type]
            agent=ollama,
            transcript=transcript,
            max_steps=3,
            result_workspace=attempt_workspace,
        )
    finally:
        transcript.close()

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(frame["kind"], frame["detailCode"]) for frame in frames] == [
        ("progress", "coding"),
        ("progress", "testing"),
        ("succeeded", "completed"),
    ]
    assert sandbox.commands == [
        ("printf script > fibonacci_script.py", 30),
        ("python3 fibonacci_script.py", 10),
    ]
    promoted = read_promoted_result(tmp_path / "jobs", job_id="job-1", attempt=1)
    assert promoted is not None
    assert promoted.artifacts[0].path == "fibonacci_script.py"
    assert len(ollama.contexts[1][-2]["tool_calls"]) == 1


def test_agent_retries_invalid_arguments_then_reports_a_specific_failure(tmp_path, capsys) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    invalid = tool_call(
        "run_shell",
        {"command": "true", "purpose": "emailing", "timeoutSeconds": 10},
    )
    transcript = PrivateTranscript(workspace)
    try:
        assert not run_agent(
            start_frame(),
            sandbox=FakeSandbox(workspace),  # type: ignore[arg-type]
            agent=ScriptedOllama([invalid, invalid, invalid]),
            transcript=transcript,
            max_steps=3,
        )
    finally:
        transcript.close()

    assert json.loads(capsys.readouterr().out)["detailCode"] == "tool_arguments_invalid"
    records = [
        json.loads(line)
        for line in workspace.joinpath("agent-transcript.jsonl").read_text().splitlines()
    ]
    assert [record["kind"] for record in records].count("tool_error") == 3


def test_artifact_promotion_rejects_symlinks_and_workspace_escape(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("report.md").write_text("safe")
    outside = tmp_path / "outside.txt"
    outside.write_text("private host data")
    workspace.joinpath("link.txt").symlink_to(outside)
    workspace.joinpath("linked-dir").symlink_to(tmp_path, target_is_directory=True)

    artifacts = safe_artifacts(
        workspace,
        ["report.md", "link.txt", "linked-dir/outside.txt", "../outside.txt"],
    )

    assert [artifact.path for artifact in artifacts] == ["report.md"]


def test_docker_sandbox_mounts_only_attempt_workspace_and_uses_internal_network(
    tmp_path,
) -> None:
    workspace = tmp_path / "jobs" / "job-1" / "attempt-1"
    workspace.mkdir(parents=True)
    sandbox = DockerSandbox(
        docker=Path("/opt/homebrew/bin/docker"),
        docker_host="unix:///private/docker.sock",
        image="radcliffe-job-sandbox:test",
        workspace=workspace,
        workspace_root=tmp_path / "jobs",
        job_id="job-1",
    )
    calls: list[list[str]] = []

    def capture(arguments, *, timeout=30, check=True):
        del timeout, check
        calls.append(list(arguments))
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    sandbox._run = capture  # type: ignore[method-assign]
    sandbox.start()
    sandbox.cleanup()

    proxy_create = next(call for call in calls if call[0] == "create" and "--read-only" in call)
    job_create = next(call for call in calls if call[0] == "create" and "--init" in call)
    mounts = [job_create[index + 1] for index, value in enumerate(job_create) if value == "--mount"]
    assert mounts == [f"type=bind,src={workspace.resolve()},dst=/workspace"]
    assert ["network", "create", "--internal", sandbox.network_name] in calls
    assert proxy_create[proxy_create.index("--network") + 1] == "bridge"
    assert [
        "network",
        "connect",
        "--alias",
        "egress",
        sandbox.network_name,
        sandbox.proxy_name,
    ] in calls
    assert "--cap-drop" in job_create and "ALL" in job_create
    assert not any(
        "TOKEN=" in value or "KEY=" in value or "SECRET=" in value for value in job_create
    )


def test_docker_sandbox_cleans_partial_start_when_proxy_is_unavailable(tmp_path) -> None:
    workspace = tmp_path / "jobs" / "job-1" / "attempt-1"
    workspace.mkdir(parents=True)
    sandbox = DockerSandbox(
        docker=Path("/opt/homebrew/bin/docker"),
        docker_host="unix:///private/docker.sock",
        image="radcliffe-job-sandbox:test",
        workspace=workspace,
        workspace_root=tmp_path / "jobs",
        job_id="job-1",
    )
    calls: list[list[str]] = []

    def fail_proxy_start(arguments, *, timeout=30, check=True):
        del timeout, check
        call = list(arguments)
        calls.append(call)
        if call == ["start", sandbox.proxy_name]:
            raise RuntimeError("proxy did not start")
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    sandbox._run = fail_proxy_start  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="proxy did not start"):
        sandbox.start()

    assert ["rm", "--force", sandbox.proxy_name] in calls
    assert ["network", "rm", sandbox.network_name] in calls
    assert not sandbox.created_proxy
    assert not sandbox.created_network
