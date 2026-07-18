from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from radcliffe_domain.catalog import SessionCatalog, SessionRecord


class WorkerLaunchError(RuntimeError):
    pass


class WorkerProcess(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


WorkerLauncher = Callable[[SessionRecord], Awaitable[WorkerProcess]]


@dataclass(slots=True)
class _OwnedWorker:
    process: WorkerProcess
    monitor: asyncio.Task[None] | None = None
    stopping: bool = False


class WorkerSupervisor:
    def __init__(
        self,
        *,
        catalog: SessionCatalog,
        principal_id: str,
        launcher: WorkerLauncher,
        stop_timeout_seconds: float = 5,
    ) -> None:
        self.catalog = catalog
        self.principal_id = principal_id
        self.launcher = launcher
        self.stop_timeout_seconds = stop_timeout_seconds
        self._owned: dict[str, _OwnedWorker] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    async def ensure(self, record: SessionRecord) -> SessionRecord:
        if record.worker_mode != "managed":
            return record
        async with self._lock:
            existing = self._owned.get(record.session_id)
            if existing is not None and existing.process.returncode is None:
                current = await self.catalog.set_worker_status(
                    principal_id=self.principal_id,
                    session_id=record.session_id,
                    worker_status="running",
                )
                assert current is not None
                return current
            await self.catalog.set_worker_status(
                principal_id=self.principal_id,
                session_id=record.session_id,
                worker_status="starting",
            )
            try:
                process = await self.launcher(record)
            except Exception as exc:
                await self.catalog.set_worker_status(
                    principal_id=self.principal_id,
                    session_id=record.session_id,
                    worker_status="failed",
                )
                raise WorkerLaunchError(
                    f"could not start media worker for {record.session_id}"
                ) from exc
            owned = _OwnedWorker(process)
            self._owned[record.session_id] = owned
            owned.monitor = asyncio.create_task(
                self._monitor(record.session_id, owned),
                name=f"radcliffe-worker-monitor-{record.session_id}",
            )
            current = await self.catalog.set_worker_status(
                principal_id=self.principal_id,
                session_id=record.session_id,
                worker_status="running",
            )
            assert current is not None
            return current

    async def _monitor(self, session_id: str, owned: _OwnedWorker) -> None:
        returncode = await owned.process.wait()
        async with self._lock:
            is_current = self._owned.get(session_id) is owned
            if is_current:
                self._owned.pop(session_id, None)
        if not is_current:
            return
        status = "stopped" if owned.stopping or self._closing or returncode == 0 else "failed"
        await self.catalog.set_worker_status(
            principal_id=self.principal_id,
            session_id=session_id,
            worker_status=status,
        )

    async def stop(self, session_id: str) -> None:
        async with self._lock:
            owned = self._owned.get(session_id)
            if owned is None:
                await self.catalog.set_worker_status(
                    principal_id=self.principal_id,
                    session_id=session_id,
                    worker_status="stopped",
                )
                return
            owned.stopping = True
            owned.process.terminate()
            monitor = owned.monitor
        try:
            await asyncio.wait_for(owned.process.wait(), timeout=self.stop_timeout_seconds)
        except TimeoutError:
            owned.process.kill()
            await owned.process.wait()
        if monitor is not None:
            await monitor

    async def reconcile(self, records: tuple[SessionRecord, ...]) -> None:
        active_ids = {record.session_id for record in records}
        for record in records:
            try:
                await self.ensure(record)
            except WorkerLaunchError:
                continue
        async with self._lock:
            stale_ids = tuple(set(self._owned) - active_ids)
        await asyncio.gather(*(self.stop(session_id) for session_id in stale_ids))

    async def close(self) -> None:
        self._closing = True
        async with self._lock:
            session_ids = tuple(self._owned)
        await asyncio.gather(*(self.stop(session_id) for session_id in session_ids))

    async def running_session_ids(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._owned))

    async def wait_for_exit(self, session_id: str) -> None:
        async with self._lock:
            owned = self._owned.get(session_id)
            monitor = owned.monitor if owned is not None else None
        if monitor is not None:
            await monitor


class SubprocessWorker:
    def __init__(self, process: asyncio.subprocess.Process, log_file: BinaryIO) -> None:
        self.process = process
        self.log_file = log_file

    @property
    def pid(self) -> int:
        assert self.process.pid is not None
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        try:
            return await self.process.wait()
        finally:
            if not self.log_file.closed:
                self.log_file.close()

    def terminate(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()

    def kill(self) -> None:
        if self.process.returncode is None:
            self.process.kill()


class LocalWorkerLauncher:
    _ENVIRONMENT_KEYS = frozenset(
        {
            "HOME",
            "PATH",
            "TMPDIR",
            "HF_HOME",
            "XDG_CACHE_HOME",
            "RADCLIFFE_SPEECH_PROVIDER",
            "RADCLIFFE_WHISPER_MODEL",
            "RADCLIFFE_WHISPER_LANGUAGE",
            "RADCLIFFE_WHISPER_INITIAL_PROMPT",
            "RADCLIFFE_TTS_VOICE",
            "RADCLIFFE_ENDPOINT_RMS",
            "RADCLIFFE_ENDPOINT_MIN_VOICE_MS",
            "RADCLIFFE_ENDPOINT_SILENCE_MS",
            "RADCLIFFE_ENDPOINT_MAX_UTTERANCE_MS",
            "RADCLIFFE_MODEL_CHUNK_CHARS",
            "RADCLIFFE_SIDECAR_URL",
        }
    )

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        workspace_root: Path,
        log_directory: Path,
        database_path: Path,
        livekit_url: str,
        token_factory: Callable[[SessionRecord], str],
        base_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.command = command
        self.workspace_root = workspace_root
        self.log_directory = log_directory
        self.database_path = database_path
        self.livekit_url = livekit_url
        self.token_factory = token_factory
        source = base_environment if base_environment is not None else os.environ
        self.base_environment = {
            key: value for key, value in source.items() if key in self._ENVIRONMENT_KEYS
        }
        if source.get("RADCLIFFE_ALLOW_CLOUD_MODELS") == "1":
            self.base_environment["RADCLIFFE_ALLOW_CLOUD_MODELS"] = "1"
            if value := source.get("RADCLIFFE_OPENAI_API_KEY"):
                self.base_environment["RADCLIFFE_OPENAI_API_KEY"] = value

    async def __call__(self, record: SessionRecord) -> WorkerProcess:
        self.log_directory.mkdir(parents=True, exist_ok=True)
        log_file = (self.log_directory / f"{record.session_id}.log").open("ab", buffering=0)
        environment = {
            **self.base_environment,
            "PYTHONUNBUFFERED": "1",
            "RADCLIFFE_SESSION_ID": record.session_id,
            "RADCLIFFE_LIVEKIT_URL": self.livekit_url,
            "RADCLIFFE_LIVEKIT_TOKEN": self.token_factory(record),
            "RADCLIFFE_DATABASE_PATH": str(self.database_path),
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.workspace_root,
                env=environment,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            log_file.close()
            raise
        return SubprocessWorker(process, log_file)
