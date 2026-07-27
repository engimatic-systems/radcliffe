from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from radcliffe_domain.jobs import (
    JobClaim,
    JobError,
    JobEventKind,
    JobLeaseError,
    JobStateError,
    JobStore,
)

logger = logging.getLogger(__name__)


class JobExecutorConfigurationError(RuntimeError):
    pass


class JobExecutorFrameError(RuntimeError):
    pass


class ExecutorEventFrame(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=lambda value: (
            value.split("_")[0] + "".join(part.title() for part in value.split("_")[1:])
        ),
        populate_by_name=True,
    )

    type: Literal["event"]
    kind: Literal["progress", "waiting", "succeeded", "failed", "cancelled"]
    detail_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    entity_ids: tuple[str, ...] = Field(default=(), max_length=50)
    artifact_ids: tuple[str, ...] = Field(default=(), max_length=50)


class JobProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    async def send(self, frame: Mapping[str, object]) -> None: ...

    async def readline(self) -> bytes: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


JobLauncher = Callable[[JobClaim], Awaitable[JobProcess]]


class JsonLineJobProcess:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        transcript: BinaryIO,
        max_transcript_bytes: int,
    ) -> None:
        self.process = process
        self.transcript = transcript
        self.max_transcript_bytes = max_transcript_bytes
        self.transcript_bytes = 0

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def send(self, frame: Mapping[str, object]) -> None:
        if self.process.stdin is None:
            raise JobExecutorFrameError("executor stdin is unavailable")
        encoded = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        self.process.stdin.write(encoded)
        await self.process.stdin.drain()

    async def readline(self) -> bytes:
        if self.process.stdout is None:
            raise JobExecutorFrameError("executor stdout is unavailable")
        line = await self.process.stdout.readline()
        if line:
            remaining = max(0, self.max_transcript_bytes - self.transcript_bytes)
            written = self.transcript.write(line[:remaining])
            self.transcript_bytes += written or 0
            self.transcript.flush()
        return line

    async def wait(self) -> int:
        try:
            return await self.process.wait()
        finally:
            if not self.transcript.closed:
                self.transcript.close()

    def terminate(self) -> None:
        if self.process.returncode is not None:
            return
        assert self.process.pid is not None
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def kill(self) -> None:
        if self.process.returncode is not None:
            return
        assert self.process.pid is not None
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


class JsonLineJobLauncher:
    _ENVIRONMENT_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE"})
    _PROVIDER_ENVIRONMENT_KEYS = frozenset({"ANTHROPIC_API_KEY"})

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        workspace_root: Path,
        max_output_bytes: int,
        base_environment: Mapping[str, str] | None = None,
        provider_environment: Mapping[str, str] | None = None,
    ) -> None:
        if not command:
            raise JobExecutorConfigurationError("job executor command is required")
        self.command = command
        self.workspace_root = workspace_root
        self.max_output_bytes = max_output_bytes
        source = base_environment if base_environment is not None else os.environ
        self.base_environment = {
            key: value for key, value in source.items() if key in self._ENVIRONMENT_KEYS
        }
        provider_source = provider_environment or {}
        unexpected = set(provider_source) - self._PROVIDER_ENVIRONMENT_KEYS
        if unexpected:
            raise JobExecutorConfigurationError("unsupported job provider environment key")
        self.provider_environment = {
            key: value
            for key, value in provider_source.items()
            if key in self._PROVIDER_ENVIRONMENT_KEYS and value
        }

    @staticmethod
    def _stage_inputs(workspace: Path, claim: JobClaim) -> None:
        if not claim.inputs:
            return
        artifact_workspace = workspace / "files"
        artifact_workspace.mkdir(mode=0o700, exist_ok=False)
        inputs_root = artifact_workspace / "inputs"
        inputs_root.mkdir(mode=0o700, exist_ok=False)
        for item in claim.inputs:
            target = inputs_root / item.name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(target, flags, 0o400)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(item.canonical_bytes)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                target.unlink(missing_ok=True)
                raise
            target.chmod(0o444)
        inputs_root.chmod(0o555)

    async def __call__(self, claim: JobClaim) -> JobProcess:
        root = self.workspace_root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        job_root = (root / claim.record.job_id).resolve()
        if not job_root.is_relative_to(root):
            raise JobExecutorConfigurationError("job workspace escaped its configured root")
        workspace = (job_root / f"attempt-{claim.record.attempt}").resolve()
        if not workspace.is_relative_to(job_root):
            raise JobExecutorConfigurationError("job attempt workspace escaped its job root")
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace.chmod(0o700)
        self._stage_inputs(workspace, claim)
        temporary = workspace / "tmp"
        temporary.mkdir(mode=0o700, exist_ok=True)
        temporary.chmod(0o700)
        transcript_path = workspace / "executor.jsonl"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(transcript_path, flags, 0o600)
        transcript = os.fdopen(descriptor, "ab", buffering=0)
        environment = {
            **self.base_environment,
            **self.provider_environment,
            "HOME": str(workspace),
            "TMPDIR": str(temporary),
            "XDG_CACHE_HOME": str(workspace / ".cache"),
            "XDG_CONFIG_HOME": str(workspace / ".config"),
            "XDG_DATA_HOME": str(workspace / ".local" / "share"),
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=workspace,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=max(64 * 1024, min(self.max_output_bytes + 1, 2 * 1024 * 1024)),
                start_new_session=True,
            )
        except Exception:
            transcript.close()
            raise
        return JsonLineJobProcess(process, transcript, self.max_output_bytes)


class JobExecutorSupervisor:
    def __init__(
        self,
        *,
        store: JobStore,
        launcher: JobLauncher,
        identity: str = "control-job-executor",
        max_concurrency: int = 2,
        poll_seconds: float = 0.25,
        heartbeat_seconds: float = 5,
        lease_seconds: int = 30,
        stop_timeout_seconds: float = 5,
        max_output_bytes: int = 1_048_576,
    ) -> None:
        if heartbeat_seconds >= lease_seconds:
            raise JobExecutorConfigurationError(
                "job executor heartbeat must be shorter than its lease"
            )
        self.store = store
        self.launcher = launcher
        self.identity = identity
        self.max_concurrency = max_concurrency
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self.stop_timeout_seconds = stop_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self._maintenance: asyncio.Task[None] | None = None
        self._attempts: set[asyncio.Task[None]] = set()
        self._sequence = 0
        self._closing = False

    def start(self) -> None:
        if self._maintenance is not None:
            return
        self._maintenance = asyncio.create_task(
            self._maintain(),
            name="radcliffe-job-executor-maintenance",
        )

    @property
    def running(self) -> bool:
        return self._maintenance is not None and not self._maintenance.done()

    async def close(self) -> None:
        self._closing = True
        if self._maintenance is not None:
            self._maintenance.cancel()
            try:
                await self._maintenance
            except asyncio.CancelledError:
                pass
        attempts = tuple(self._attempts)
        for task in attempts:
            task.cancel()
        if attempts:
            await asyncio.gather(*attempts, return_exceptions=True)
        self._attempts.clear()

    async def _maintain(self) -> None:
        while not self._closing:
            finished = {task for task in self._attempts if task.done()}
            for task in finished:
                self._attempts.remove(task)
                if not task.cancelled() and task.exception() is not None:
                    logger.error("background job attempt stopped unexpectedly")
            accepted = False
            while len(self._attempts) < self.max_concurrency and not self._closing:
                self._sequence += 1
                lease_owner = f"{self.identity}:{self._sequence}"
                claim = await self.store.claim_next(
                    lease_owner=lease_owner,
                    lease_seconds=self.lease_seconds,
                )
                if claim is None:
                    break
                accepted = True
                task = asyncio.create_task(
                    self._drive(claim, lease_owner),
                    name=f"radcliffe-job-attempt-{claim.record.job_id}",
                )
                self._attempts.add(task)
            if not accepted:
                await asyncio.sleep(self.poll_seconds)

    async def _drive(self, claim: JobClaim, lease_owner: str) -> None:
        process: JobProcess | None = None
        read_task: asyncio.Task[bytes] | None = None
        try:
            try:
                process = await self.launcher(claim)
                await process.send(
                    {
                        "type": "start",
                        "schemaVersion": 1,
                        "jobId": claim.record.job_id,
                        "attempt": claim.record.attempt,
                        "instruction": claim.instruction,
                        "userMessage": claim.user_message,
                        "relatedLinks": [
                            {"label": item.label, "locator": item.locator}
                            for item in claim.related_links
                        ],
                        "inputs": [
                            {
                                "kind": item.kind,
                                "name": item.name,
                                "path": f"inputs/{item.name}",
                                "sourceReference": item.source_reference,
                                "producerJobId": item.producer_job_id,
                                "entityId": item.entity_id,
                                "artifactId": item.artifact_id,
                                "sha256": item.sha256,
                                "byteLength": item.byte_length,
                            }
                            for item in claim.inputs
                        ],
                        "followUps": [
                            {"id": item.follow_up_id, "instruction": item.instruction}
                            for item in claim.follow_ups
                        ],
                        "limits": {
                            "budgetUsd": (
                                claim.record.budget_microusd / 1_000_000
                                if claim.record.budget_microusd is not None
                                else None
                            ),
                            "maxRuntimeSeconds": claim.record.max_runtime_seconds,
                            "maxOutputBytes": self.max_output_bytes,
                        },
                    }
                )
            except Exception:
                if process is not None:
                    await self._stop(process)
                await self._publish_failure(claim.record.job_id, lease_owner, "executor_failed")
                return
            delivered = {item.follow_up_id for item in claim.follow_ups}
            output_bytes = 0
            loop = asyncio.get_running_loop()
            deadline = loop.time() + claim.record.max_runtime_seconds
            next_heartbeat = loop.time() + self.heartbeat_seconds
            read_task = asyncio.create_task(process.readline())
            while True:
                now = loop.time()
                if now >= next_heartbeat:
                    heartbeat = await self.store.heartbeat(
                        job_id=claim.record.job_id,
                        lease_owner=lease_owner,
                        lease_seconds=self.lease_seconds,
                    )
                    next_heartbeat = now + self.heartbeat_seconds
                    if heartbeat.record.cancel_requested_at is not None:
                        await self._stop(process)
                        await self._publish_cancelled(
                            claim.record.job_id, lease_owner, "cancelled_by_user"
                        )
                        return
                    for follow_up in heartbeat.follow_ups:
                        if follow_up.follow_up_id in delivered:
                            continue
                        await process.send(
                            {
                                "type": "follow_up",
                                "schemaVersion": 1,
                                "id": follow_up.follow_up_id,
                                "instruction": follow_up.instruction,
                            }
                        )
                        delivered.add(follow_up.follow_up_id)
                remaining = deadline - now
                if remaining <= 0:
                    await self._stop(process)
                    await self._publish_cancelled(
                        claim.record.job_id, lease_owner, "cancelled_by_timeout"
                    )
                    return
                done, _pending = await asyncio.wait(
                    {read_task},
                    timeout=min(max(0, next_heartbeat - now), remaining),
                )
                if not done:
                    continue
                try:
                    line = read_task.result()
                except (ValueError, asyncio.LimitOverrunError):
                    await self._stop(process)
                    await self._publish_failure(
                        claim.record.job_id, lease_owner, "resource_exhausted"
                    )
                    return
                if not line:
                    await process.wait()
                    await self._publish_failure(claim.record.job_id, lease_owner, "executor_failed")
                    return
                output_bytes += len(line)
                if output_bytes > self.max_output_bytes:
                    await self._stop(process)
                    await self._publish_failure(
                        claim.record.job_id, lease_owner, "resource_exhausted"
                    )
                    return
                try:
                    frame = ExecutorEventFrame.model_validate_json(line)
                except ValidationError:
                    await self._stop(process)
                    await self._publish_failure(claim.record.job_id, lease_owner, "executor_failed")
                    return
                kind = JobEventKind(frame.kind)
                try:
                    await self.store.publish(
                        job_id=claim.record.job_id,
                        lease_owner=lease_owner,
                        kind=kind,
                        detail_code=frame.detail_code,
                        entity_ids=frame.entity_ids,
                        artifact_ids=frame.artifact_ids,
                    )
                except JobStateError:
                    await self._stop(process)
                    await self._publish_cancelled(
                        claim.record.job_id, lease_owner, "cancelled_by_user"
                    )
                    return
                except JobError:
                    await self._stop(process)
                    await self._publish_failure(claim.record.job_id, lease_owner, "executor_failed")
                    return
                if kind is JobEventKind.PROGRESS:
                    read_task = asyncio.create_task(process.readline())
                    continue
                await self._stop(process)
                return
        except (JobLeaseError, JobStateError):
            if process is not None:
                await self._stop(process)
        except Exception:
            if process is not None:
                await self._stop(process)
            await self._publish_failure(claim.record.job_id, lease_owner, "executor_failed")
        except asyncio.CancelledError:
            if process is not None:
                await self._stop(process)
            raise
        finally:
            if read_task is not None and not read_task.done():
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)

    async def _publish_failure(self, job_id: str, lease_owner: str, detail_code: str) -> None:
        try:
            await self.store.publish(
                job_id=job_id,
                lease_owner=lease_owner,
                kind=JobEventKind.FAILED,
                detail_code=detail_code,
            )
        except JobStateError:
            await self._publish_cancelled(job_id, lease_owner, "cancelled_by_user")
        except (JobError, JobLeaseError):
            return

    async def _publish_cancelled(self, job_id: str, lease_owner: str, detail_code: str) -> None:
        try:
            await self.store.publish(
                job_id=job_id,
                lease_owner=lease_owner,
                kind=JobEventKind.CANCELLED,
                detail_code=detail_code,
            )
        except (JobError, JobLeaseError):
            return

    async def _stop(self, process: JobProcess) -> None:
        if process.returncode is not None:
            await process.wait()
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self.stop_timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
