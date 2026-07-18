import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_control.workers import LocalWorkerLauncher, WorkerLaunchError, WorkerSupervisor
from radcliffe_domain.catalog import SessionCatalog
from radcliffe_domain.store import EventStore


class FakeProcess:
    next_pid = 100

    def __init__(self) -> None:
        self._returncode: int | None = None
        self.finished = asyncio.Event()
        self.terminated = False
        self.killed = False
        self._pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        await self.finished.wait()
        assert self._returncode is not None
        return self._returncode

    def finish(self, returncode: int) -> None:
        self._returncode = returncode
        self.finished.set()

    def terminate(self) -> None:
        self.terminated = True
        self.finish(-15)

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)


@pytest.mark.asyncio
async def test_supervisor_starts_exactly_one_worker_for_concurrent_retries(tmp_path) -> None:
    path = tmp_path / "workers.sqlite3"
    await EventStore(path).migrate()
    catalog = SessionCatalog(path)
    record, _created = await catalog.create(principal_id="owner")
    launched: list[FakeProcess] = []

    async def launch(_record):
        process = FakeProcess()
        launched.append(process)
        return process

    supervisor = WorkerSupervisor(catalog=catalog, principal_id="owner", launcher=launch)
    results = await asyncio.gather(*(supervisor.ensure(record) for _ in range(12)))

    assert len(launched) == 1
    assert {result.worker_status for result in results} == {"running"}
    assert await supervisor.running_session_ids() == (record.session_id,)
    await supervisor.stop(record.session_id)
    assert launched[0].terminated
    stopped = await catalog.get(principal_id="owner", session_id=record.session_id)
    assert stopped is not None
    assert stopped.worker_status == "stopped"


@pytest.mark.asyncio
async def test_worker_failure_is_visible_and_reconcile_cleans_stale_sessions(tmp_path) -> None:
    path = tmp_path / "workers.sqlite3"
    await EventStore(path).migrate()
    catalog = SessionCatalog(path)
    first, _created = await catalog.create(principal_id="owner")
    second, _created = await catalog.create(principal_id="owner")
    launched: dict[str, FakeProcess] = {}

    async def launch(record):
        process = FakeProcess()
        launched[record.session_id] = process
        return process

    supervisor = WorkerSupervisor(catalog=catalog, principal_id="owner", launcher=launch)
    await supervisor.reconcile((first, second))
    launched[first.session_id].finish(7)
    await asyncio.wait_for(supervisor.wait_for_exit(first.session_id), timeout=1)
    failed = await catalog.get(principal_id="owner", session_id=first.session_id)
    assert failed is not None
    assert failed.worker_status == "failed"
    await supervisor.reconcile((first,))

    assert launched[second.session_id].terminated
    assert await supervisor.running_session_ids() == (first.session_id,)
    await supervisor.close()


@pytest.mark.asyncio
async def test_launch_failure_sets_durable_failed_status(tmp_path) -> None:
    path = tmp_path / "workers.sqlite3"
    await EventStore(path).migrate()
    catalog = SessionCatalog(path)
    record, _created = await catalog.create(principal_id="owner")

    async def fail(_record):
        raise OSError("executable missing")

    supervisor = WorkerSupervisor(catalog=catalog, principal_id="owner", launcher=fail)
    with pytest.raises(WorkerLaunchError, match="could not start"):
        await supervisor.ensure(record)

    failed = await catalog.get(principal_id="owner", session_id=record.session_id)
    assert failed is not None
    assert failed.worker_status == "failed"


@pytest.mark.asyncio
async def test_managed_session_reconciliation_respects_status_and_idle_cutoff(tmp_path) -> None:
    path = tmp_path / "workers.sqlite3"
    await EventStore(path).migrate()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    catalog = SessionCatalog(path, clock=lambda: now)
    recent, _created = await catalog.create(principal_id="owner")
    explicit, _created = await catalog.create(principal_id="owner", session_id="external")

    active = await catalog.managed_active_since(
        principal_id="owner",
        active_since=now - timedelta(minutes=1),
    )
    assert active == (recent,)
    await catalog.update(
        principal_id="owner",
        session_id=recent.session_id,
        title=None,
        status="ended",
    )
    assert (
        await catalog.managed_active_since(
            principal_id="owner",
            active_since=now - timedelta(minutes=1),
        )
        == ()
    )
    assert explicit.worker_mode == "external"


def test_local_launcher_does_not_inherit_control_plane_secrets(tmp_path) -> None:
    launcher = LocalWorkerLauncher(
        command=("uv",),
        workspace_root=tmp_path,
        log_directory=tmp_path / "logs",
        database_path=tmp_path / "db.sqlite3",
        livekit_url="ws://127.0.0.1:7880",
        token_factory=lambda _record: "short-lived-room-token",
        base_environment={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin",
            "RADCLIFFE_BOOTSTRAP_TOKEN": "must-not-pass",
            "RADCLIFFE_LIVEKIT_API_SECRET": "must-not-pass",
            "RADCLIFFE_OPENAI_API_KEY": "disabled-cloud-key",
            "RADCLIFFE_SPEECH_PROVIDER": "local",
            "RADCLIFFE_WHISPER_VERIFICATION_MODEL": "verification-model",
            "RADCLIFFE_STT_CAPTURE_ENABLED": "1",
            "RADCLIFFE_STT_CAPTURE_PLAN": str(tmp_path / "capture-plan.json"),
            "RADCLIFFE_STT_CAPTURE_DIRECTORY": str(tmp_path / "captures"),
        },
    )

    assert launcher.base_environment == {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin",
        "RADCLIFFE_SPEECH_PROVIDER": "local",
        "RADCLIFFE_WHISPER_VERIFICATION_MODEL": "verification-model",
        "RADCLIFFE_STT_CAPTURE_ENABLED": "1",
        "RADCLIFFE_STT_CAPTURE_PLAN": str(tmp_path / "capture-plan.json"),
        "RADCLIFFE_STT_CAPTURE_DIRECTORY": str(tmp_path / "captures"),
    }
