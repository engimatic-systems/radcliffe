from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
import queue
import signal
import stat
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    ServerToolUseBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from radcliffe_control.job_results import (
    PromotedJobResult,
    generated_artifact_source,
    read_workspace_related_links,
    write_promoted_result,
)
from radcliffe_control.sandbox_agent import (
    HAIKU_MODEL_ID,
    DockerSandbox,
    FailJobArguments,
    FinishJobArguments,
    FinishJobFinding,
    FinishJobSource,
    FollowUpFrame,
    PreviewPageArguments,
    PrivateTranscript,
    RunShellArguments,
    StartFrame,
    emit_event,
    promoted_findings,
    promoted_sources,
    safe_artifacts,
    workspace_relative_artifact,
)

MAX_MARKDOWN_CHARS = 1_048_576
MAX_READ_CHARS = 8_192
MAX_PROMPT_BYTES = 64 * 1024
MCP_SERVER_NAME = "radcliffe_jobs"
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
MAX_CORRECTIVE_TURNS = 2
CORRECTIVE_PROMPT = (
    "The job is not complete because no terminal tool succeeded. Use run_command to create "
    "or verify the requested workspace artifact without printing document bodies, then call "
    "publish_markdown for a Markdown result, finish_job for another artifact type, or fail_job "
    "if grounded attempts prove the objective cannot be completed. Do not return an ordinary "
    "final response."
)


class EventSink(Protocol):
    def __call__(
        self,
        kind: Literal["progress", "succeeded", "failed"],
        detail_code: str,
        *,
        entity_ids: Sequence[str] = (),
        artifact_ids: Sequence[str] = (),
    ) -> None: ...


class SdkClient(Protocol):
    async def __aenter__(self) -> SdkClient: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...

    async def query(self, prompt: str, session_id: str = "default") -> None: ...

    def receive_response(self) -> Any: ...


SdkClientFactory = Callable[[ClaudeAgentOptions], SdkClient]


class CopyTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_path: str = Field(alias="sourcePath", min_length=1, max_length=256)
    destination_path: str = Field(alias="destinationPath", min_length=1, max_length=256)

    @field_validator("source_path")
    @classmethod
    def valid_source(cls, value: str) -> str:
        normalized = workspace_relative_artifact(value)
        if not normalized.startswith("pages/") or not normalized.casefold().endswith(".txt"):
            raise ValueError("sourcePath must name extracted page text")
        return normalized

    @field_validator("destination_path")
    @classmethod
    def valid_destination(cls, value: str) -> str:
        normalized = workspace_relative_artifact(value)
        if not normalized.casefold().endswith(".md"):
            raise ValueError("destinationPath must name a Markdown file")
        return normalized


class ReadArtifactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = Field(min_length=1, max_length=256)
    start_character: int = Field(default=0, alias="startCharacter", ge=0)
    max_characters: int = Field(default=4_096, alias="maxCharacters", ge=1, le=MAX_READ_CHARS)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return workspace_relative_artifact(value)


class PublishMarkdownArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=4_000)
    sources: tuple[FinishJobSource, ...] = Field(default=(), max_length=20)
    findings: tuple[FinishJobFinding, ...] = Field(default=(), max_length=20)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        normalized = workspace_relative_artifact(value)
        if not normalized.casefold().endswith(".md"):
            raise ValueError("path must name a Markdown file")
        return normalized


@dataclass(frozen=True, slots=True)
class TerminalOutcome:
    succeeded: bool
    detail_code: str
    entity_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()


def _json_tool_result(payload: Mapping[str, object], *, error: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            }
        ]
    }
    if error:
        result["isError"] = True
    return result


def _safe_existing_file(workspace: Path, value: str) -> Path:
    relative = PurePosixPath(workspace_relative_artifact(value))
    raw = workspace.joinpath(*relative.parts)
    if any(
        workspace.joinpath(*relative.parts[:index]).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ):
        raise ValueError("artifact path traverses a symbolic link")
    candidate = raw.resolve()
    if not candidate.is_relative_to(workspace.resolve()):
        raise ValueError("artifact path escaped the workspace")
    info = candidate.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
        raise ValueError("artifact is not a bounded regular file")
    return candidate


def _write_new_file(workspace: Path, value: str, payload: bytes) -> Path:
    relative = PurePosixPath(workspace_relative_artifact(value))
    candidate = workspace.joinpath(*relative.parts)
    parent = candidate.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("artifact parent escaped the workspace")
    if any(
        workspace.joinpath(*relative.parts[:index]).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ):
        raise ValueError("artifact path traverses a symbolic link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    return candidate


class AgentSdkTools:
    def __init__(
        self,
        *,
        start: StartFrame,
        sandbox: DockerSandbox,
        attempt_workspace: Path,
        transcript: PrivateTranscript,
        event_sink: EventSink,
    ) -> None:
        self.start = start
        self.sandbox = sandbox
        self.workspace = sandbox.workspace.resolve()
        self.attempt_workspace = attempt_workspace.resolve()
        self.transcript = transcript
        self.event_sink = event_sink
        self.terminal: TerminalOutcome | None = None

    async def download_page(self, arguments: Mapping[str, object]) -> dict[str, object]:
        try:
            parsed = PreviewPageArguments.model_validate(arguments)
        except ValidationError:
            return _json_tool_result(
                {"error": "invalid_arguments", "guidance": "Supply one public URL."},
                error=True,
            )
        self.event_sink("progress", "searching")
        code, output, truncated = await asyncio.to_thread(
            self.sandbox.preview_page,
            parsed.url,
            parsed.query,
        )
        self.transcript.append(
            "sdk_page_preview",
            {
                "url": parsed.url,
                "query": parsed.query,
                "exitCode": code,
                "output": output,
                "truncated": truncated,
            },
        )
        if code != 0:
            return _json_tool_result(
                {"error": "download_failed", "exitCode": code, "detail": output},
                error=True,
            )
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return _json_tool_result({"error": "invalid_preview_result"}, error=True)
        return _json_tool_result(
            payload if isinstance(payload, dict) else {"error": "invalid_preview_result"},
            error=not isinstance(payload, dict),
        )

    async def copy_text(self, arguments: Mapping[str, object]) -> dict[str, object]:
        try:
            parsed = CopyTextArguments.model_validate(arguments)
            source = _safe_existing_file(self.workspace, parsed.source_path)
            payload = source.read_bytes()
            if len(payload) > MAX_MARKDOWN_CHARS:
                raise ValueError("source text is too large for a readable Markdown artifact")
            payload.decode("utf-8")
            _write_new_file(self.workspace, parsed.destination_path, payload)
        except (OSError, UnicodeDecodeError, ValueError, ValidationError) as error:
            return _json_tool_result(
                {"error": "copy_failed", "detail": str(error)[:300]}, error=True
            )
        self.event_sink("progress", "downloading")
        self.transcript.append(
            "sdk_copy_text",
            {
                "sourcePath": parsed.source_path,
                "destinationPath": parsed.destination_path,
                "byteLength": len(payload),
            },
        )
        return _json_tool_result(
            {
                "path": parsed.destination_path,
                "byteLength": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "copiedExactly": True,
            }
        )

    async def run_command(self, arguments: Mapping[str, object]) -> dict[str, object]:
        try:
            parsed = RunShellArguments.model_validate(arguments)
        except ValidationError:
            return _json_tool_result(
                {
                    "error": "invalid_arguments",
                    "guidance": (
                        "Supply a sandbox command, a valid purpose, and an optional timeout."
                    ),
                },
                error=True,
            )
        self.event_sink("progress", parsed.purpose)
        code, output, truncated = await asyncio.to_thread(
            self.sandbox.execute,
            parsed.command,
            timeout_seconds=parsed.timeout_seconds,
        )
        self.transcript.append(
            "sdk_run_command",
            {
                "purpose": parsed.purpose,
                "exitCode": code,
                "output": output,
                "truncated": truncated,
            },
        )
        return _json_tool_result(
            {
                "exitCode": code,
                "output": output,
                "truncated": truncated,
                "guidance": (
                    "Inspect named files with another bounded command or publish a finished "
                    "Markdown path."
                    if code == 0
                    else "Correct the command using its bounded diagnostics."
                ),
            },
            error=code != 0,
        )

    async def read_artifact(self, arguments: Mapping[str, object]) -> dict[str, object]:
        try:
            parsed = ReadArtifactArguments.model_validate(arguments)
            candidate = _safe_existing_file(self.workspace, parsed.path)
            payload = candidate.read_bytes()
            text = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError, ValidationError) as error:
            return _json_tool_result(
                {"error": "read_failed", "detail": str(error)[:300]}, error=True
            )
        start = min(parsed.start_character, len(text))
        end = min(len(text), start + parsed.max_characters)
        self.event_sink("progress", "analyzing")
        return _json_tool_result(
            {
                "path": parsed.path,
                "startCharacter": start,
                "endCharacter": end,
                "totalCharacters": len(text),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content": text[start:end],
                "truncated": end < len(text),
            }
        )

    async def finish_job(self, arguments: Mapping[str, object]) -> dict[str, object]:
        if self.terminal is not None:
            return _json_tool_result({"error": "job_already_finished"}, error=True)
        try:
            parsed = FinishJobArguments.model_validate(arguments)
            promoted_paths = tuple(
                dict.fromkeys((*parsed.artifact_paths, *(source.path for source in parsed.sources)))
            )
            artifacts = safe_artifacts(self.workspace, promoted_paths)
            if not promoted_paths or len(artifacts) != len(promoted_paths):
                raise ValueError("finish_job named a missing or unsafe artifact")
            declared_sources = promoted_sources(
                self.start.job_id,
                self.workspace,
                parsed.sources,
                artifacts,
            )
            declared_paths = {source.path for source in declared_sources}
            generated_sources = tuple(
                generated
                for artifact in artifacts
                if artifact.path not in declared_paths
                and (generated := generated_artifact_source(self.start.job_id, artifact))
                is not None
            )
            sources = (*declared_sources, *generated_sources)
            findings = promoted_findings(self.start.job_id, parsed.findings, sources)
            result = PromotedJobResult(
                schemaVersion=1,
                jobId=self.start.job_id,
                summary=parsed.summary.strip(),
                artifacts=artifacts,
                sources=sources,
                findings=findings,
                relatedLinks=read_workspace_related_links(self.workspace),
            )
            write_promoted_result(self.attempt_workspace, result)
        except (OSError, ValueError, ValidationError) as error:
            return _json_tool_result(
                {"error": "finish_rejected", "detail": str(error)[:300]}, error=True
            )
        result_id = hashlib.sha256(f"{self.start.job_id}:{parsed.summary}".encode()).hexdigest()[
            :16
        ]
        self.terminal = TerminalOutcome(
            succeeded=True,
            detail_code="completed",
            artifact_ids=(
                f"artifact:result:{result_id}",
                *(artifact.artifact_id for artifact in artifacts),
            ),
            entity_ids=(
                *(source.entity_id for source in sources),
                *(finding.entity_id for finding in findings),
            ),
        )
        self.transcript.append(
            "sdk_finish",
            {
                "summary": parsed.summary,
                "artifactPaths": list(promoted_paths),
                "sourceCount": len(sources),
                "findingCount": len(findings),
            },
        )
        return _json_tool_result({"accepted": True, "artifactCount": len(artifacts)})

    async def publish_markdown(self, arguments: Mapping[str, object]) -> dict[str, object]:
        if self.terminal is not None:
            return _json_tool_result({"error": "job_already_finished"}, error=True)
        try:
            parsed = PublishMarkdownArguments.model_validate(arguments)
            candidate = _safe_existing_file(self.workspace, parsed.path)
            payload = candidate.read_bytes()
            if not payload or len(payload) > MAX_MARKDOWN_CHARS:
                raise ValueError("published Markdown must be non-empty and bounded")
            payload.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError, ValidationError) as error:
            return _json_tool_result(
                {"error": "publish_rejected", "detail": str(error)[:300]},
                error=True,
            )
        result = await self.finish_job(
            {
                "summary": parsed.summary,
                "artifactPaths": [parsed.path],
                "sources": [source.model_dump(by_alias=True) for source in parsed.sources],
                "findings": [finding.model_dump(by_alias=True) for finding in parsed.findings],
            }
        )
        if result.get("isError") is not True:
            self.transcript.append(
                "sdk_publish_markdown",
                {
                    "path": parsed.path,
                    "byteLength": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
        return result

    async def fail_job(self, arguments: Mapping[str, object]) -> dict[str, object]:
        if self.terminal is not None:
            return _json_tool_result({"error": "job_already_finished"}, error=True)
        try:
            parsed = FailJobArguments.model_validate(arguments)
            result = PromotedJobResult(
                schemaVersion=1,
                jobId=self.start.job_id,
                summary=parsed.summary,
                artifacts=(),
                sources=(),
                findings=(),
                relatedLinks=read_workspace_related_links(self.workspace),
            )
            write_promoted_result(self.attempt_workspace, result)
        except (OSError, ValueError, ValidationError) as error:
            return _json_tool_result(
                {"error": "failure_rejected", "detail": str(error)[:300]}, error=True
            )
        result_id = hashlib.sha256(f"{self.start.job_id}:{parsed.summary}".encode()).hexdigest()[
            :16
        ]
        self.terminal = TerminalOutcome(
            succeeded=False,
            detail_code=parsed.reason_code,
            artifact_ids=(f"artifact:result:{result_id}",),
        )
        self.transcript.append(
            "sdk_fail",
            {"summary": parsed.summary, "reasonCode": parsed.reason_code},
        )
        return _json_tool_result({"accepted": True})

    def sdk_tools(self) -> list[Any]:
        @tool(
            "download_page",
            "Download one selected public page through Radcliffe's credential-free policy "
            "sandbox. Saves full HTML and Trafilatura-extracted text under pages/ and returns a "
            "small relevance preview. Use WebSearch first, then call this once for the first "
            "adequate result.",
            PreviewPageArguments.model_json_schema(by_alias=True),
        )
        async def download_page(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.download_page(arguments)

        @tool(
            "copy_text_to_markdown",
            "Copy a downloaded pages/*.txt extraction byte-for-byte into a new Markdown file. "
            "Use this for verbatim or complete source artifacts instead of regenerating text.",
            CopyTextArguments.model_json_schema(by_alias=True),
        )
        async def copy_text(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.copy_text(arguments)

        @tool(
            "run_command",
            "Run a shell command inside Radcliffe's disposable Docker workspace, never on the "
            "host. Use ordinary CLI or Python tools to transform files into files. Keep document "
            "bodies out of stdout; only bounded diagnostics are returned to model context.",
            RunShellArguments.model_json_schema(by_alias=True),
        )
        async def run_command(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.run_command(arguments)

        @tool(
            "read_artifact",
            "Read a bounded character range from a job artifact for verification. Returns the "
            "full file hash and length without putting an unbounded file into context.",
            ReadArtifactArguments.model_json_schema(by_alias=True),
        )
        async def read_artifact(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.read_artifact(arguments)

        @tool(
            "publish_markdown",
            "Atomically finish a successful Markdown job by naming an existing UTF-8 Markdown "
            "path plus summary and optional source/finding metadata. Document content is read "
            "from the workspace and never accepted in this tool call.",
            PublishMarkdownArguments.model_json_schema(by_alias=True),
        )
        async def publish_markdown(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.publish_markdown(arguments)

        @tool(
            "finish_job",
            "Finish only after the requested work exists and has been checked. Name every useful "
            "artifact and identify canonical Markdown sources with their public locators.",
            FinishJobArguments.model_json_schema(by_alias=True),
        )
        async def finish_job(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.finish_job(arguments)

        @tool(
            "fail_job",
            "End unsuccessfully only when grounded attempts show that the request cannot be "
            "completed within the available tools or policy.",
            FailJobArguments.model_json_schema(by_alias=True),
        )
        async def fail_job(arguments: Mapping[str, object]) -> dict[str, object]:
            return await self.fail_job(arguments)

        return [
            download_page,
            copy_text,
            run_command,
            read_artifact,
            publish_markdown,
            finish_job,
            fail_job,
        ]


def _read_prompt(path: Path) -> str:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > MAX_PROMPT_BYTES:
        raise ValueError("the Agent SDK system prompt must be a bounded regular file")
    prompt = resolved.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("the Agent SDK system prompt must not be empty")
    return prompt


def _prompt(start: StartFrame) -> str:
    parts = ["Foreground instruction:\n" + start.instruction]
    if start.user_message:
        parts.append("Verbatim current user message:\n" + start.user_message)
    if start.related_links:
        parts.append(
            "Related links from prior jobs:\n"
            + "\n".join(f"- {item.label}: {item.locator}" for item in start.related_links)
        )
    if start.inputs:
        parts.append(
            "Authorized immutable inputs already staged in the workspace:\n"
            + "\n".join(
                f"- /workspace/{item.path} ({item.kind}; requested as {item.source_reference})"
                for item in start.inputs
            )
        )
    parts.extend("User follow-up:\n" + item.instruction for item in start.follow_ups)
    return "\n\n".join(parts)


def _message_payload(message: object) -> Mapping[str, object]:
    if dataclasses.is_dataclass(message) and not isinstance(message, type):
        payload = dataclasses.asdict(message)
        if isinstance(payload, dict):
            return payload
    return {"type": type(message).__name__}


def _initialized_tools(payload: Mapping[str, object]) -> set[str] | None:
    if payload.get("subtype") != "init":
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return set()
    tools = data.get("tools")
    if not isinstance(tools, list):
        return set()
    return {item for item in tools if isinstance(item, str)}


def _result_failure(result: ResultMessage | None) -> str:
    if result is None:
        return "model_request_failed"
    if result.subtype == "error_max_budget_usd":
        return "budget_exhausted"
    if result.subtype == "error_max_turns":
        return "step_limit_exhausted"
    if result.is_error:
        return "model_request_failed"
    return "model_no_action"


async def run_agent_sdk(
    start: StartFrame,
    *,
    sandbox: DockerSandbox,
    attempt_workspace: Path,
    transcript: PrivateTranscript,
    system_prompt: str,
    model: str,
    effort: Literal["low", "medium", "high"],
    max_turns: int | None,
    max_budget_usd: float,
    client_factory: SdkClientFactory = ClaudeSDKClient,
    event_sink: EventSink = emit_event,
) -> bool:
    requested_budget = start.limits.budget_usd or max_budget_usd
    budget = min(requested_budget, max_budget_usd)
    if budget <= 0:
        event_sink("failed", "budget_exhausted")
        return False
    tools = AgentSdkTools(
        start=start,
        sandbox=sandbox,
        attempt_workspace=attempt_workspace,
        transcript=transcript,
        event_sink=event_sink,
    )
    sdk_tools = tools.sdk_tools()
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME,
        version="1.0.0",
        tools=sdk_tools,
    )
    custom_names = [f"{MCP_TOOL_PREFIX}{item.name}" for item in sdk_tools]
    available_tools = ["WebSearch", "WebFetch", *custom_names]
    required_native_tools = {"WebSearch", "WebFetch"}
    options = ClaudeAgentOptions(
        tools=available_tools,
        allowed_tools=available_tools,
        disallowed_tools=[
            "Agent",
            "AskUserQuestion",
            "Bash",
            "Edit",
            "Glob",
            "Grep",
            "Read",
            "Skill",
            "TaskCreate",
            "TaskUpdate",
            "ToolSearch",
            "Write",
        ],
        permission_mode="dontAsk",
        system_prompt=system_prompt,
        mcp_servers={MCP_SERVER_NAME: server},
        strict_mcp_config=True,
        model=model,
        effort=effort,
        max_turns=max_turns,
        max_budget_usd=budget,
        cwd=sandbox.workspace,
        setting_sources=[],
        skills=[],
        plugins=[],
        extra_args={
            "disable-slash-commands": None,
            "no-session-persistence": None,
        },
        stderr=lambda line: transcript.append("sdk_stderr", {"message": line[:2_000]}),
    )
    transcript.append(
        "sdk_effective_limits",
        {
            "model": model,
            "effort": effort,
            "maxTurns": max_turns,
            "maxBudgetUsd": budget,
            "tools": available_tools,
        },
    )
    follow_ups: queue.SimpleQueue[FollowUpFrame] = queue.SimpleQueue()
    threading.Thread(target=_read_follow_ups, args=(follow_ups,), daemon=True).start()
    last_result: ResultMessage | None = None
    observed_tool_ids: set[str] = set()
    initialized = False
    corrective_turns = 0
    try:
        async with client_factory(options) as client:
            await client.query(_prompt(start), session_id=start.job_id)
            while True:
                async for message in client.receive_response():
                    payload = _message_payload(message)
                    transcript.append("sdk_message", payload)
                    initialized_tools = _initialized_tools(payload)
                    if initialized_tools is not None:
                        initialized = True
                        missing_tools = required_native_tools - initialized_tools
                        if missing_tools:
                            transcript.append(
                                "sdk_tool_contract_error",
                                {"missingTools": sorted(missing_tools)},
                            )
                            event_sink("failed", "executor_failed")
                            return False
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if not isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                                continue
                            if block.id in observed_tool_ids:
                                continue
                            observed_tool_ids.add(block.id)
                            if block.name in {"WebSearch", "web_search"}:
                                event_sink("progress", "searching")
                            elif block.name in {"WebFetch", "web_fetch"}:
                                event_sink("progress", "downloading")
                    elif isinstance(message, ResultMessage):
                        last_result = message
                if tools.terminal is not None:
                    break
                pending: list[FollowUpFrame] = []
                while not follow_ups.empty():
                    pending.append(follow_ups.get())
                if pending:
                    for item in pending:
                        transcript.append(
                            "follow_up",
                            {"id": item.id, "instruction": item.instruction},
                        )
                    await client.query(
                        "\n\n".join("User follow-up:\n" + item.instruction for item in pending),
                        session_id=start.job_id,
                    )
                    continue
                if (
                    _result_failure(last_result) == "model_no_action"
                    and corrective_turns < MAX_CORRECTIVE_TURNS
                ):
                    corrective_turns += 1
                    transcript.append(
                        "sdk_corrective_turn",
                        {"reason": "terminal_tool_required"},
                    )
                    await client.query(CORRECTIVE_PROMPT, session_id=start.job_id)
                    continue
                break
    except ClaudeSDKError as error:
        transcript.append("sdk_error", {"type": type(error).__name__})
        event_sink("failed", "model_request_failed")
        return False
    if not initialized:
        transcript.append("sdk_tool_contract_error", {"missingInitialization": True})
        event_sink("failed", "executor_failed")
        return False
    if tools.terminal is not None:
        terminal = tools.terminal
        event_sink(
            "succeeded" if terminal.succeeded else "failed",
            terminal.detail_code,
            entity_ids=terminal.entity_ids,
            artifact_ids=terminal.artifact_ids,
        )
        return terminal.succeeded
    event_sink("failed", _result_failure(last_result))
    return False


def _read_follow_ups(target: queue.SimpleQueue[FollowUpFrame]) -> None:
    try:
        for line in sys.stdin:
            try:
                target.put(FollowUpFrame.model_validate_json(line))
            except ValidationError:
                continue
    except OSError:
        return


def _default_prompt_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "claude-agent-job-prompt.txt"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--docker", type=Path, required=True)
    value.add_argument("--docker-host", required=True)
    value.add_argument("--image", required=True)
    value.add_argument("--workspace-root", type=Path, required=True)
    value.add_argument("--provider", choices=("anthropic",), required=True)
    value.add_argument("--model", default=HAIKU_MODEL_ID)
    value.add_argument("--effort", choices=("low", "medium", "high"), default="low")
    value.add_argument("--max-turns", type=int)
    value.add_argument("--max-model-cost-usd", type=float, default=0.25)
    value.add_argument("--system-prompt-file", type=Path, default=_default_prompt_path())
    return value


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    sandbox: DockerSandbox | None = None
    transcript: PrivateTranscript | None = None
    try:
        if options.max_turns is not None and not 1 <= options.max_turns <= 64:
            raise ValueError("Agent SDK max turns must be between one and 64")
        if not 0 < options.max_model_cost_usd <= 2:
            raise ValueError("Agent SDK model budget must be between zero and two dollars")
        start = StartFrame.model_validate_json(sys.stdin.readline())
        attempt_workspace = Path.cwd().resolve()
        artifact_workspace = attempt_workspace / "files"
        artifact_workspace.mkdir(mode=0o700, exist_ok=True)
        if artifact_workspace.is_symlink() or not artifact_workspace.is_dir():
            raise ValueError("artifact workspace is unavailable")
        transcript = PrivateTranscript(attempt_workspace)
        transcript.append("start", start.model_dump(by_alias=True))
        sandbox = DockerSandbox(
            docker=options.docker,
            docker_host=options.docker_host,
            image=options.image,
            workspace=artifact_workspace,
            workspace_root=options.workspace_root,
            job_id=start.job_id,
        )

        def terminate(_signum: int, _frame: object) -> None:
            if sandbox is not None:
                sandbox.cleanup()
            raise SystemExit(143)

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            emit_event("failed", "model_unavailable")
            return 1
        system_prompt = _read_prompt(options.system_prompt_file)
        sandbox.start()
        succeeded = asyncio.run(
            run_agent_sdk(
                start,
                sandbox=sandbox,
                attempt_workspace=attempt_workspace,
                transcript=transcript,
                system_prompt=system_prompt,
                model=options.model,
                effort=options.effort,
                max_turns=options.max_turns,
                max_budget_usd=options.max_model_cost_usd,
            )
        )
        return 0 if succeeded else 1
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        if transcript is not None:
            transcript.append(
                "executor_error",
                {"code": "executor_failed", "type": type(error).__name__},
            )
        emit_event("failed", "executor_failed")
        return 1
    finally:
        if sandbox is not None:
            sandbox.cleanup()
        if transcript is not None:
            transcript.close()


if __name__ == "__main__":
    raise SystemExit(main())
