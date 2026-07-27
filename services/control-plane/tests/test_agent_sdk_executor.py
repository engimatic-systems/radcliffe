from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal, cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage
from radcliffe_control.agent_sdk_executor import (
    MCP_TOOL_PREFIX,
    AgentSdkTools,
    EventSink,
    SdkClient,
    parser,
    run_agent_sdk,
)
from radcliffe_control.job_results import read_promoted_result
from radcliffe_control.sandbox_agent import DockerSandbox, PrivateTranscript, StartFrame


def start_frame(*, budget_usd: float | None = None) -> StartFrame:
    return StartFrame.model_validate(
        {
            "type": "start",
            "schemaVersion": 1,
            "jobId": "job-agent-sdk-test",
            "attempt": 1,
            "instruction": "Download the first available commentary and save it as Markdown.",
            "userMessage": "Get the commentary and save the exact result for me.",
            "relatedLinks": [{"label": "Commentary", "locator": "https://example.test/commentary"}],
            "followUps": [],
            "limits": {
                "budgetUsd": budget_usd,
                "maxRuntimeSeconds": 300,
                "maxOutputBytes": 65_536,
            },
        }
    )


class FakeSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.previewed: list[tuple[str, str]] = []
        self.commands: list[tuple[str, int]] = []

    def preview_page(self, url: str, query: str) -> tuple[int, str, bool]:
        self.previewed.append((url, query))
        pages = self.workspace / "pages"
        pages.mkdir(exist_ok=True)
        pages.joinpath("commentary.html").write_text("<p>Downloaded commentary.</p>")
        pages.joinpath("commentary.txt").write_text("Downloaded commentary.\n")
        return (
            0,
            json.dumps(
                {
                    "schemaVersion": 1,
                    "title": "Example Commentary",
                    "htmlPath": "pages/commentary.html",
                    "textPath": "pages/commentary.txt",
                    "preview": "Downloaded commentary.",
                    "untrusted": True,
                }
            ),
            False,
        )

    def execute(
        self,
        command: str,
        *,
        timeout_seconds: int = 180,
    ) -> tuple[int, str, bool]:
        self.commands.append((command, timeout_seconds))
        return 0, "verification passed\n", False


class CapturingEvents:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []

    def __call__(
        self,
        kind: Literal["progress", "succeeded", "failed"],
        detail_code: str,
        *,
        entity_ids: tuple[str, ...] = (),
        artifact_ids: tuple[str, ...] = (),
    ) -> None:
        self.items.append((kind, detail_code, tuple(entity_ids), tuple(artifact_ids)))


def test_sdk_cli_defaults_to_pinned_haiku_and_a_quarter_dollar_per_job() -> None:
    options = parser().parse_args(
        [
            "--docker",
            "/usr/bin/docker",
            "--docker-host",
            "unix:///tmp/docker.sock",
            "--image",
            "radcliffe-job-sandbox:test",
            "--workspace-root",
            "/tmp/jobs",
            "--provider",
            "anthropic",
        ]
    )

    assert options.model == "claude-haiku-4-5-20251001"
    assert options.max_model_cost_usd == 0.25
    assert options.max_turns is None


@pytest.mark.asyncio
async def test_sdk_tools_download_copy_and_promote_a_canonical_source(tmp_path: Path) -> None:
    attempt = tmp_path / "job-agent-sdk-test" / "attempt-1"
    files = attempt / "files"
    files.mkdir(parents=True)
    transcript = PrivateTranscript(attempt)
    events = CapturingEvents()
    sandbox = FakeSandbox(files)
    tools = AgentSdkTools(
        start=start_frame(),
        sandbox=cast(DockerSandbox, sandbox),
        attempt_workspace=attempt,
        transcript=transcript,
        event_sink=cast(EventSink, events),
    )
    try:
        downloaded = await tools.download_page(
            {"url": "https://example.test/commentary", "query": "Gettysburg commentary"}
        )
        copied = await tools.copy_text(
            {
                "sourcePath": "pages/commentary.txt",
                "destinationPath": "gettysburg-commentary.md",
            }
        )
        finished = await tools.publish_markdown(
            {
                "path": "gettysburg-commentary.md",
                "summary": "Downloaded the first available commentary.",
                "sources": [
                    {
                        "name": "Example Commentary",
                        "locator": "https://example.test/commentary",
                        "path": "gettysburg-commentary.md",
                    }
                ],
                "findings": [],
            }
        )
    finally:
        transcript.close()

    assert "isError" not in downloaded
    assert "isError" not in copied
    assert "isError" not in finished
    assert files.joinpath("gettysburg-commentary.md").read_text() == "Downloaded commentary.\n"
    assert tools.terminal is not None and tools.terminal.succeeded
    promoted = read_promoted_result(
        tmp_path,
        job_id="job-agent-sdk-test",
        attempt=1,
    )
    assert promoted is not None
    assert [artifact.path for artifact in promoted.artifacts] == ["gettysburg-commentary.md"]
    assert promoted.sources[0].locator == "https://example.test/commentary"
    assert [item[:2] for item in events.items] == [
        ("progress", "searching"),
        ("progress", "downloading"),
    ]


@pytest.mark.asyncio
async def test_sdk_command_and_publish_tools_preserve_the_workspace_boundary(
    tmp_path: Path,
) -> None:
    attempt = tmp_path / "attempt-1"
    files = attempt / "files"
    files.mkdir(parents=True)
    transcript = PrivateTranscript(attempt)
    sandbox = FakeSandbox(files)
    tools = AgentSdkTools(
        start=start_frame(),
        sandbox=cast(DockerSandbox, sandbox),
        attempt_workspace=attempt,
        transcript=transcript,
        event_sink=cast(EventSink, CapturingEvents()),
    )
    try:
        command = await tools.run_command(
            {
                "command": "python -m trafilatura --input pages/commentary.html > report.md",
                "purpose": "analyzing",
                "timeoutSeconds": 45,
            }
        )
        escaped = await tools.publish_markdown({"path": "../outside.md", "summary": "No."})
        missing = await tools.publish_markdown({"path": "missing.md", "summary": "No."})
        files.joinpath("report.md").write_text("Generated report.\n")
        published = await tools.publish_markdown(
            {"path": "report.md", "summary": "Generated the requested report."}
        )
    finally:
        transcript.close()

    assert "isError" not in command
    assert sandbox.commands == [
        (
            "python -m trafilatura --input pages/commentary.html > report.md",
            45,
        )
    ]
    assert escaped["isError"] is True
    assert missing["isError"] is True
    assert "isError" not in published
    assert files.joinpath("report.md").read_text() == "Generated report.\n"
    assert not tmp_path.joinpath("outside.md").exists()


class FakeClient:
    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.prompts: list[tuple[str, str]] = []

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.prompts.append((prompt, session_id))

    async def receive_response(self) -> AsyncIterator[object]:
        configured = self.options.tools or []
        yield SystemMessage(subtype="init", data={"tools": list(configured)})
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="job-agent-sdk-test",
            total_cost_usd=0.01,
            result="I forgot to finish.",
        )


@pytest.mark.asyncio
async def test_sdk_runner_exposes_only_bounded_tools_and_caps_local_budget(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt-1"
    files = attempt / "files"
    files.mkdir(parents=True)
    transcript = PrivateTranscript(attempt)
    events = CapturingEvents()
    captured: list[FakeClient] = []

    def factory(options: ClaudeAgentOptions) -> SdkClient:
        client = FakeClient(options)
        captured.append(client)
        return cast(SdkClient, client)

    try:
        succeeded = await run_agent_sdk(
            start_frame(budget_usd=0.50),
            sandbox=cast(DockerSandbox, FakeSandbox(files)),
            attempt_workspace=attempt,
            transcript=transcript,
            system_prompt="Finish the job with the bounded Radcliffe tools.",
            model="claude-sonnet-test",
            effort="low",
            max_turns=6,
            max_budget_usd=0.25,
            client_factory=factory,
            event_sink=cast(EventSink, events),
        )
    finally:
        transcript.close()

    assert not succeeded
    options = captured[0].options
    assert options.max_budget_usd == 0.25
    assert options.max_turns == 6
    assert options.model == "claude-sonnet-test"
    assert options.effort == "low"
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.skills == []
    assert options.extra_args == {
        "disable-slash-commands": None,
        "no-session-persistence": None,
    }
    assert options.tools is not None
    assert "WebSearch" in options.tools
    assert "WebFetch" in options.tools
    assert f"{MCP_TOOL_PREFIX}run_command" in options.tools
    assert f"{MCP_TOOL_PREFIX}publish_markdown" in options.tools
    assert f"{MCP_TOOL_PREFIX}finish_job" in options.tools
    assert f"{MCP_TOOL_PREFIX}write_markdown" not in options.tools
    assert "Bash" not in options.tools
    assert "Read" not in options.tools
    assert "Write" not in options.tools
    assert captured[0].prompts[0][1] == "job-agent-sdk-test"
    assert "Verbatim current user message" in captured[0].prompts[0][0]
    assert "Get the commentary and save the exact result for me." in captured[0].prompts[0][0]
    assert "Commentary: https://example.test/commentary" in captured[0].prompts[0][0]
    assert len(captured[0].prompts) == 3
    assert all(
        "The job is not complete because no terminal tool succeeded." in prompt
        for prompt, _session in captured[0].prompts[1:]
    )
    assert events.items[-1][:2] == ("failed", "model_no_action")


@pytest.mark.asyncio
async def test_sdk_runner_fails_when_runtime_initialization_omits_native_web_tools(
    tmp_path: Path,
) -> None:
    attempt = tmp_path / "attempt-1"
    files = attempt / "files"
    files.mkdir(parents=True)
    transcript = PrivateTranscript(attempt)
    events = CapturingEvents()

    class MissingToolsClient(FakeClient):
        async def receive_response(self) -> AsyncIterator[object]:
            yield SystemMessage(subtype="init", data={"tools": []})

    try:
        succeeded = await run_agent_sdk(
            start_frame(),
            sandbox=cast(DockerSandbox, FakeSandbox(files)),
            attempt_workspace=attempt,
            transcript=transcript,
            system_prompt="Finish with tools.",
            model="claude-sonnet-test",
            effort="low",
            max_turns=6,
            max_budget_usd=0.10,
            client_factory=lambda options: cast(SdkClient, MissingToolsClient(options)),
            event_sink=cast(EventSink, events),
        )
    finally:
        transcript.close()

    assert not succeeded
    assert events.items[-1][:2] == ("failed", "executor_failed")
    assert "sdk_tool_contract_error" in attempt.joinpath("agent-transcript.jsonl").read_text()
