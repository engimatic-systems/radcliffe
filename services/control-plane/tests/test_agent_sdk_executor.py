from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal, cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from radcliffe_control.agent_sdk_executor import (
    MCP_TOOL_PREFIX,
    AgentSdkTools,
    EventSink,
    SdkClient,
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
        finished = await tools.finish_job(
            {
                "summary": "Downloaded the first available commentary.",
                "artifactPaths": ["gettysburg-commentary.md"],
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
async def test_sdk_artifact_tools_reject_escape_and_overwrite(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt-1"
    files = attempt / "files"
    files.mkdir(parents=True)
    transcript = PrivateTranscript(attempt)
    tools = AgentSdkTools(
        start=start_frame(),
        sandbox=cast(DockerSandbox, FakeSandbox(files)),
        attempt_workspace=attempt,
        transcript=transcript,
        event_sink=cast(EventSink, CapturingEvents()),
    )
    try:
        escaped = await tools.write_markdown({"path": "../outside.md", "content": "no"})
        first = await tools.write_markdown({"path": "report.md", "content": "first"})
        overwritten = await tools.write_markdown({"path": "report.md", "content": "second"})
    finally:
        transcript.close()

    assert escaped["isError"] is True
    assert "isError" not in first
    assert overwritten["isError"] is True
    assert files.joinpath("report.md").read_text() == "first"
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

    async def receive_response(self) -> AsyncIterator[ResultMessage]:
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
            max_budget_usd=0.10,
            client_factory=factory,
            event_sink=cast(EventSink, events),
        )
    finally:
        transcript.close()

    assert not succeeded
    options = captured[0].options
    assert options.max_budget_usd == 0.10
    assert options.max_turns == 6
    assert options.model == "claude-sonnet-test"
    assert options.effort == "low"
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.skills == []
    assert options.extra_args == {
        "bare": None,
        "disable-slash-commands": None,
        "no-session-persistence": None,
    }
    assert options.tools is not None
    assert "WebSearch" in options.tools
    assert "WebFetch" in options.tools
    assert f"{MCP_TOOL_PREFIX}finish_job" in options.tools
    assert "Bash" not in options.tools
    assert "Read" not in options.tools
    assert "Write" not in options.tools
    assert captured[0].prompts[0][1] == "job-agent-sdk-test"
    assert events.items[-1][:2] == ("failed", "model_no_action")
