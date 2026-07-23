import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_control.job_executor import (
    JobExecutorConfigurationError,
    JobExecutorSupervisor,
    JsonLineJobLauncher,
)
from radcliffe_domain.catalog import SessionCatalog
from radcliffe_domain.jobs import JobEventKind, JobStatus, JobStore
from radcliffe_domain.store import EventStore


class FakeJobProcess:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.output: asyncio.Queue[bytes] = asyncio.Queue()
        self._returncode: int | None = None
        self.finished = asyncio.Event()
        self.terminated = False
        self.killed = False

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def send(self, frame) -> None:
        self.frames.append(dict(frame))

    async def readline(self) -> bytes:
        return await self.output.get()

    async def wait(self) -> int:
        await self.finished.wait()
        assert self._returncode is not None
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self.finish(-15)

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)

    def emit(self, frame: dict[str, object]) -> None:
        self.output.put_nowait(json.dumps(frame).encode() + b"\n")

    def emit_raw(self, line: bytes) -> None:
        self.output.put_nowait(line)

    def finish(self, returncode: int) -> None:
        if self._returncode is not None:
            return
        self._returncode = returncode
        self.finished.set()
        self.output.put_nowait(b"")


class FakeLauncher:
    def __init__(self) -> None:
        self.claims = []
        self.processes: list[FakeJobProcess] = []

    async def __call__(self, claim):
        process = FakeJobProcess()
        self.claims.append(claim)
        self.processes.append(process)
        return process


async def store_and_session(tmp_path, *, clock=None):
    database = tmp_path / "executor.sqlite3"
    await EventStore(database).migrate()
    session, _created = await SessionCatalog(database).create(
        principal_id="owner", session_id="session-1"
    )
    store = JobStore(database, **({"clock": clock} if clock is not None else {}))
    return store, session


async def eventually(predicate, *, deadline_seconds: float = 2) -> None:
    async with asyncio.timeout(deadline_seconds):
        while not await predicate():  # noqa: ASYNC110 - poll the durable store under test
            await asyncio.sleep(0.01)


async def status_is(store: JobStore, job_id: str, expected: JobStatus) -> bool:
    record = await store.get(principal_id="owner", job_id=job_id)
    return record is not None and record.status is expected


@pytest.mark.asyncio
async def test_supervisor_reports_progress_and_success_without_blocking_acceptance(
    tmp_path,
) -> None:
    store, session = await store_and_session(tmp_path)
    launcher = FakeLauncher()
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        lease_seconds=2,
    )
    supervisor.start()
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private executor instruction",
        budget_microusd=500_000,
    )
    assert accepted.record.status is JobStatus.QUEUED

    async def launched() -> bool:
        return bool(launcher.processes and launcher.processes[0].frames)

    await eventually(launched)
    process = launcher.processes[0]
    assert process.frames[0]["type"] == "start"
    assert process.frames[0]["instruction"] == "private executor instruction"
    process.emit({"type": "event", "kind": "progress", "detailCode": "searching"})
    process.emit(
        {
            "type": "event",
            "kind": "succeeded",
            "detailCode": "completed",
            "entityIds": ["paper:one"],
            "artifactIds": ["artifact:report"],
        }
    )
    await eventually(lambda: status_is(store, accepted.record.job_id, JobStatus.SUCCEEDED))
    await supervisor.close()

    events = await store.events(accepted.record.job_id)
    assert [event.kind for event in events] == [
        JobEventKind.ACCEPTED,
        JobEventKind.STARTED,
        JobEventKind.PROGRESS,
        JobEventKind.SUCCEEDED,
    ]
    assert "private executor instruction" not in repr(events)
    assert events[-1].entity_ids == ("paper:one",)


@pytest.mark.asyncio
async def test_live_follow_up_is_delivered_once_and_cancellation_terminates_process(
    tmp_path,
) -> None:
    store, session = await store_and_session(tmp_path)
    launcher = FakeLauncher()
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        lease_seconds=2,
    )
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private primary",
    )
    supervisor.start()

    async def launched() -> bool:
        return bool(launcher.processes and launcher.processes[0].frames)

    await eventually(launched)
    process = launcher.processes[0]
    await store.add_follow_up(
        principal_id="owner",
        job_id=accepted.record.job_id,
        instruction="private live follow-up",
        idempotency_key="follow-up-1",
    )

    async def follow_up_delivered() -> bool:
        return any(frame.get("type") == "follow_up" for frame in process.frames)

    await eventually(follow_up_delivered)
    await asyncio.sleep(0.08)
    follow_up_frames = [frame for frame in process.frames if frame.get("type") == "follow_up"]
    assert len(follow_up_frames) == 1
    assert follow_up_frames[0]["instruction"] == "private live follow-up"
    await store.request_cancel(
        principal_id="owner",
        job_id=accepted.record.job_id,
        idempotency_key="cancel-1",
    )
    await eventually(lambda: status_is(store, accepted.record.job_id, JobStatus.CANCELLED))
    await supervisor.close()

    assert process.terminated
    events = await store.events(accepted.record.job_id)
    assert events[-1].kind is JobEventKind.CANCELLED
    assert events[-1].detail_code == "cancelled_by_user"
    assert "private live follow-up" not in repr(events)


@pytest.mark.asyncio
async def test_waiting_job_requeues_with_private_follow_up_in_a_new_attempt(tmp_path) -> None:
    store, session = await store_and_session(tmp_path)
    launcher = FakeLauncher()
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        lease_seconds=2,
    )
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private question-producing task",
    )
    supervisor.start()

    async def first_launched() -> bool:
        return bool(launcher.processes and launcher.processes[0].frames)

    await eventually(first_launched)
    launcher.processes[0].emit(
        {"type": "event", "kind": "waiting", "detailCode": "waiting_for_user"}
    )
    await eventually(lambda: status_is(store, accepted.record.job_id, JobStatus.WAITING))
    assert launcher.processes[0].terminated
    await store.add_follow_up(
        principal_id="owner",
        job_id=accepted.record.job_id,
        instruction="private answer to the executor question",
        idempotency_key="follow-up-1",
    )

    async def second_launched() -> bool:
        return len(launcher.processes) == 2 and bool(launcher.processes[1].frames)

    await eventually(second_launched)
    start = launcher.processes[1].frames[0]
    assert start["attempt"] == 2
    assert start["followUps"][0]["instruction"] == "private answer to the executor question"
    launcher.processes[1].emit({"type": "event", "kind": "succeeded", "detailCode": "completed"})
    await eventually(lambda: status_is(store, accepted.record.job_id, JobStatus.SUCCEEDED))
    await supervisor.close()
    assert [event.kind for event in await store.events(accepted.record.job_id)] == [
        JobEventKind.ACCEPTED,
        JobEventKind.STARTED,
        JobEventKind.WAITING,
        JobEventKind.FOLLOW_UP_ADDED,
        JobEventKind.STARTED,
        JobEventKind.SUCCEEDED,
    ]


@pytest.mark.asyncio
async def test_malformed_and_excessive_executor_output_fail_two_jobs_independently(
    tmp_path,
) -> None:
    store, session = await store_and_session(tmp_path)
    launcher = FakeLauncher()
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        max_concurrency=2,
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        lease_seconds=2,
        max_output_bytes=80,
    )
    jobs = [
        await store.accept(
            principal_id="owner",
            session_id=session.session_id,
            generation=1,
            idempotency_key=f"accept-{index}",
            instruction=f"private job {index}",
        )
        for index in range(2)
    ]
    supervisor.start()

    async def both_launched() -> bool:
        return len(launcher.processes) == 2 and all(item.frames for item in launcher.processes)

    await eventually(both_launched)
    launcher.processes[0].emit_raw(b"not-json\n")
    launcher.processes[1].emit_raw(b"x" * 81 + b"\n")
    for job in jobs:
        await eventually(
            lambda job_id=job.record.job_id: status_is(store, job_id, JobStatus.FAILED)
        )
    await supervisor.close()

    detail_codes = {(await store.events(job.record.job_id))[-1].detail_code for job in jobs}
    assert detail_codes == {"executor_failed", "resource_exhausted"}


@pytest.mark.asyncio
async def test_expired_attempt_is_recovered_after_supervisor_restart(tmp_path) -> None:
    current = [datetime(2026, 7, 18, 12, tzinfo=UTC)]
    store, session = await store_and_session(tmp_path, clock=lambda: current[0])
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="recover me",
    )
    abandoned = await store.claim_next(lease_owner="old-executor", lease_seconds=30)
    assert abandoned is not None
    current[0] += timedelta(seconds=31)

    launcher = FakeLauncher()
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        lease_seconds=30,
    )
    supervisor.start()

    async def recovered() -> bool:
        return bool(launcher.claims)

    await eventually(recovered)
    assert launcher.claims[0].recovered
    launcher.processes[0].emit({"type": "event", "kind": "succeeded", "detailCode": "completed"})
    await eventually(lambda: status_is(store, accepted.record.job_id, JobStatus.SUCCEEDED))
    await supervisor.close()
    assert [event.kind for event in await store.events(accepted.record.job_id)] == [
        JobEventKind.ACCEPTED,
        JobEventKind.STARTED,
        JobEventKind.LEASE_RECOVERED,
        JobEventKind.SUCCEEDED,
    ]


@pytest.mark.asyncio
async def test_executor_crash_and_runtime_timeout_have_stable_terminal_results(tmp_path) -> None:
    store, session = await store_and_session(tmp_path)
    launcher = FakeLauncher()
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        max_concurrency=2,
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        lease_seconds=2,
    )
    crashed = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-crash",
        instruction="crash test",
    )
    timed_out = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-timeout",
        instruction="timeout test",
        max_runtime_seconds=1,
    )
    supervisor.start()

    async def both_launched() -> bool:
        return len(launcher.processes) == 2 and all(item.frames for item in launcher.processes)

    await eventually(both_launched)
    crash_index = next(
        index
        for index, claim in enumerate(launcher.claims)
        if claim.record.job_id == crashed.record.job_id
    )
    timeout_index = next(
        index
        for index, claim in enumerate(launcher.claims)
        if claim.record.job_id == timed_out.record.job_id
    )
    launcher.processes[crash_index].finish(7)
    await eventually(lambda: status_is(store, crashed.record.job_id, JobStatus.FAILED))
    await eventually(
        lambda: status_is(store, timed_out.record.job_id, JobStatus.CANCELLED),
        deadline_seconds=2,
    )
    await supervisor.close()

    assert (await store.events(crashed.record.job_id))[-1].detail_code == "executor_failed"
    assert (await store.events(timed_out.record.job_id))[-1].detail_code == ("cancelled_by_timeout")
    assert launcher.processes[timeout_index].terminated


@pytest.mark.asyncio
async def test_real_json_line_process_uses_private_workspace_and_scrubbed_environment(
    tmp_path,
) -> None:
    store, session = await store_and_session(tmp_path)
    helper = (
        "import json,os,pathlib,sys; "
        "frame=json.loads(sys.stdin.readline()); "
        "pathlib.Path('observed.json').write_text(json.dumps({"
        "'instruction':frame['instruction'],'secret':os.getenv('RADCLIFFE_INTERNAL_TOKEN'),"
        "'provider_secret':os.getenv('ANTHROPIC_API_KEY'),"
        "'home':os.getenv('HOME')})); "
        "print(json.dumps({'type':'event','kind':'succeeded','detailCode':'completed'}),flush=True)"
    )
    workspace_root = tmp_path / "private-jobs"
    launcher = JsonLineJobLauncher(
        command=(sys.executable, "-c", helper),
        workspace_root=workspace_root,
        max_output_bytes=4096,
        base_environment={
            "PATH": "/usr/bin",
            "RADCLIFFE_INTERNAL_TOKEN": "must-not-inherit",
            "ANTHROPIC_API_KEY": "must-not-inherit",
        },
    )
    assert "private process instruction" not in " ".join(launcher.command)
    assert launcher.base_environment == {"PATH": "/usr/bin"}
    supervisor = JobExecutorSupervisor(
        store=store,
        launcher=launcher,
        poll_seconds=0.01,
        heartbeat_seconds=0.05,
        lease_seconds=2,
        max_output_bytes=4096,
    )
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-real",
        instruction="private process instruction",
    )
    supervisor.start()
    await eventually(lambda: status_is(store, accepted.record.job_id, JobStatus.SUCCEEDED))
    await supervisor.close()

    attempt = workspace_root / accepted.record.job_id / "attempt-1"
    observed = json.loads((attempt / "observed.json").read_text())
    assert observed["instruction"] == "private process instruction"
    assert observed["secret"] is None
    assert observed["provider_secret"] is None
    assert observed["home"] == str(attempt)
    assert workspace_root.stat().st_mode & 0o777 == 0o700
    assert attempt.stat().st_mode & 0o777 == 0o700
    transcript = attempt / "executor.jsonl"
    assert transcript.stat().st_mode & 0o777 == 0o600
    assert "private process instruction" not in transcript.read_text()
    assert "must-not-inherit" not in transcript.read_text()


def test_job_launcher_passes_only_an_explicit_provider_key_to_the_trusted_broker(
    tmp_path,
) -> None:
    launcher = JsonLineJobLauncher(
        command=(sys.executable, "-c", "pass"),
        workspace_root=tmp_path / "jobs",
        max_output_bytes=4096,
        base_environment={
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "must-not-inherit-implicitly",
            "RADCLIFFE_INTERNAL_TOKEN": "must-not-inherit",
        },
        provider_environment={"ANTHROPIC_API_KEY": "explicit-broker-key"},
    )

    assert launcher.base_environment == {"PATH": "/usr/bin"}
    assert launcher.provider_environment == {"ANTHROPIC_API_KEY": "explicit-broker-key"}

    with pytest.raises(JobExecutorConfigurationError, match="unsupported"):
        JsonLineJobLauncher(
            command=(sys.executable, "-c", "pass"),
            workspace_root=tmp_path / "other-jobs",
            max_output_bytes=4096,
            provider_environment={"RADCLIFFE_INTERNAL_TOKEN": "forbidden"},
        )
