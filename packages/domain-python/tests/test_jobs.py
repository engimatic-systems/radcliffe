import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_domain.catalog import SessionCatalog
from radcliffe_domain.jobs import (
    JobConflictError,
    JobEventKind,
    JobLeaseError,
    JobNotificationDisposition,
    JobNotificationPolicy,
    JobRelatedLink,
    JobStateError,
    JobStatus,
    JobStore,
)
from radcliffe_domain.store import EventStore


async def job_store(tmp_path, *, clock=None, ids=None):
    path = tmp_path / "jobs.sqlite3"
    await EventStore(path).migrate()
    catalog = SessionCatalog(path)
    session, _created = await catalog.create(principal_id="owner", session_id="session-1")
    identifiers = iter(ids or ("job-1", "job-2", "job-3"))
    store = JobStore(
        path,
        id_factory=lambda: next(identifiers),
        **({"clock": clock} if clock is not None else {}),
    )
    return path, session, store


@pytest.mark.asyncio
async def test_accept_is_idempotent_and_events_exclude_private_instruction(tmp_path) -> None:
    _path, session, store = await job_store(tmp_path)

    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private six-month lab-paper search",
        notification_policy=JobNotificationPolicy.NEXT_SAFE_PAUSE,
        budget_microusd=250_000,
    )
    replay = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private six-month lab-paper search",
        notification_policy=JobNotificationPolicy.NEXT_SAFE_PAUSE,
        budget_microusd=250_000,
    )

    assert not accepted.replayed
    assert replay.replayed
    assert replay.record == accepted.record
    assert accepted.record.status is JobStatus.QUEUED
    assert accepted.record.max_runtime_seconds == 300
    assert accepted.record.revision == 1
    assert not hasattr(accepted.record, "instruction")
    events = await store.events(accepted.record.job_id)
    assert [(event.revision, event.kind) for event in events] == [(1, JobEventKind.ACCEPTED)]
    assert "private" not in repr(events)

    with pytest.raises(JobConflictError, match="different input"):
        await store.accept(
            principal_id="owner",
            session_id=session.session_id,
            generation=1,
            idempotency_key="accept-1",
            instruction="different private request",
        )


@pytest.mark.asyncio
async def test_concurrent_claim_has_one_owner_and_expired_lease_is_recoverable(tmp_path) -> None:
    now = datetime(2026, 7, 18, 9, tzinfo=UTC)
    current = [now]
    _path, session, store = await job_store(tmp_path, clock=lambda: current[0])
    await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="research in a separate context",
    )

    claims = await asyncio.gather(
        *(store.claim_next(lease_owner=f"worker-{index}") for index in range(12))
    )
    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].instruction == "research in a separate context"
    assert claimed[0].record.attempt == 1
    assert not claimed[0].recovered

    current[0] += timedelta(seconds=31)
    recovered = await store.claim_next(lease_owner="recovery-worker")
    assert recovered is not None
    assert recovered.recovered
    assert recovered.record.attempt == 2
    assert recovered.record.lease_owner == "recovery-worker"
    assert [event.kind for event in await store.events(recovered.record.job_id)] == [
        JobEventKind.ACCEPTED,
        JobEventKind.STARTED,
        JobEventKind.LEASE_RECOVERED,
    ]


@pytest.mark.asyncio
async def test_follow_up_priority_and_cancel_are_idempotent_and_ordered(tmp_path) -> None:
    _path, session, store = await job_store(tmp_path)
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="analyze papers",
    )
    job_id = accepted.record.job_id

    priority = await store.set_priority(
        principal_id="owner", job_id=job_id, priority=0, idempotency_key="priority-1"
    )
    priority_replay = await store.set_priority(
        principal_id="owner", job_id=job_id, priority=0, idempotency_key="priority-1"
    )
    follow_up = await store.add_follow_up(
        principal_id="owner",
        job_id=job_id,
        instruction="also inspect the methods",
        idempotency_key="follow-up-1",
    )
    cancelled = await store.request_cancel(
        principal_id="owner", job_id=job_id, idempotency_key="cancel-1"
    )

    assert priority.record.revision == 2
    assert priority_replay.replayed
    assert priority_replay.record.revision == 2
    assert follow_up.record.revision == 3
    assert cancelled.record.revision == 4
    assert cancelled.record.status is JobStatus.CANCELLED
    with pytest.raises(JobStateError, match="terminal"):
        await store.add_follow_up(
            principal_id="owner",
            job_id=job_id,
            instruction="too late",
            idempotency_key="follow-up-2",
        )
    with pytest.raises(JobConflictError, match="different input"):
        await store.set_priority(
            principal_id="owner", job_id=job_id, priority=1, idempotency_key="priority-1"
        )

    events = await store.events(job_id)
    assert [event.kind for event in events] == [
        JobEventKind.ACCEPTED,
        JobEventKind.PRIORITY_CHANGED,
        JobEventKind.FOLLOW_UP_ADDED,
        JobEventKind.CANCELLED,
    ]
    assert "methods" not in repr(events)


@pytest.mark.asyncio
async def test_running_cancel_wins_over_late_success_and_worker_acknowledges(tmp_path) -> None:
    _path, session, store = await job_store(tmp_path)
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="long analysis",
    )
    claim = await store.claim_next(lease_owner="worker-1")
    assert claim is not None
    await store.request_cancel(
        principal_id="owner", job_id=accepted.record.job_id, idempotency_key="cancel-1"
    )

    with pytest.raises(JobStateError, match="cancellation was requested"):
        await store.publish(
            job_id=accepted.record.job_id,
            lease_owner="worker-1",
            kind=JobEventKind.SUCCEEDED,
            detail_code="completed",
        )
    cancelled = await store.publish(
        job_id=accepted.record.job_id,
        lease_owner="worker-1",
        kind=JobEventKind.CANCELLED,
        detail_code="cancelled_by_user",
    )
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.lease_owner is None


@pytest.mark.asyncio
async def test_failed_job_retry_is_idempotent_and_claims_a_fresh_attempt(tmp_path) -> None:
    _path, session, store = await job_store(tmp_path)
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="download and extract headlines",
        user_message="Get the Hacker News headlines for me.",
        related_links=(
            JobRelatedLink(label="Second story", locator="https://example.test/second"),
        ),
    )
    claim = await store.claim_next(lease_owner="worker-1")
    assert claim is not None
    assert claim.user_message == "Get the Hacker News headlines for me."
    assert claim.related_links == (
        JobRelatedLink(label="Second story", locator="https://example.test/second"),
    )
    failed = await store.publish(
        job_id=accepted.record.job_id,
        lease_owner="worker-1",
        kind=JobEventKind.FAILED,
        detail_code="model_provider_unavailable",
    )
    assert failed.status is JobStatus.FAILED

    retried = await store.retry(
        principal_id="owner",
        job_id=accepted.record.job_id,
        idempotency_key="retry-1",
        user_message="Download the second story from that list.",
        related_links=(
            JobRelatedLink(label="Second story", locator="https://example.test/second"),
        ),
    )
    replay = await store.retry(
        principal_id="owner",
        job_id=accepted.record.job_id,
        idempotency_key="retry-1",
        user_message="Download the second story from that list.",
        related_links=(
            JobRelatedLink(label="Second story", locator="https://example.test/second"),
        ),
    )

    assert retried.record.status is JobStatus.QUEUED
    assert replay.replayed
    second_claim = await store.claim_next(lease_owner="worker-2")
    assert second_claim is not None
    assert second_claim.record.job_id == accepted.record.job_id
    assert second_claim.record.attempt == 2
    assert second_claim.user_message == "Download the second story from that list."
    assert second_claim.related_links == (
        JobRelatedLink(label="Second story", locator="https://example.test/second"),
    )
    events = await store.events(accepted.record.job_id)
    assert [(event.kind, event.detail_code) for event in events] == [
        (JobEventKind.ACCEPTED, "accepted"),
        (JobEventKind.STARTED, "started"),
        (JobEventKind.FAILED, "model_provider_unavailable"),
        (JobEventKind.ACCEPTED, "retried"),
        (JobEventKind.STARTED, "started"),
    ]
    with pytest.raises(JobStateError, match="only a failed"):
        await store.retry(
            principal_id="owner",
            job_id=accepted.record.job_id,
            idempotency_key="retry-while-running",
        )


@pytest.mark.asyncio
async def test_restart_replays_metadata_and_claim_keeps_private_context_separate(tmp_path) -> None:
    path, session, store = await job_store(tmp_path)
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private primary instruction",
    )
    await store.add_follow_up(
        principal_id="owner",
        job_id=accepted.record.job_id,
        instruction="private follow-up",
        idempotency_key="follow-up-1",
    )

    reopened = JobStore(path)
    record = await reopened.get(principal_id="owner", job_id=accepted.record.job_id)
    assert record is not None
    assert not hasattr(record, "instruction")
    claim = await reopened.claim_next(lease_owner="worker-after-restart")
    assert claim is not None
    assert claim.instruction == "private primary instruction"
    assert [item.instruction for item in claim.follow_ups] == ["private follow-up"]
    assert claim.follow_ups[0].follow_up_id.startswith("job-follow-up:")
    heartbeat = await reopened.heartbeat(
        job_id=claim.record.job_id,
        lease_owner="worker-after-restart",
        lease_seconds=60,
    )
    assert heartbeat.record.lease_owner == "worker-after-restart"
    assert heartbeat.follow_ups == claim.follow_ups
    with pytest.raises(JobLeaseError, match="another executor"):
        await reopened.publish(
            job_id=claim.record.job_id,
            lease_owner="wrong-worker",
            kind=JobEventKind.PROGRESS,
            detail_code="searching",
        )
    with pytest.raises(ValueError, match="unregistered job event detail code"):
        await reopened.publish(
            job_id=claim.record.job_id,
            lease_owner="worker-after-restart",
            kind=JobEventKind.PROGRESS,
            detail_code="private_output_fragment",
        )
    completed = await reopened.publish(
        job_id=claim.record.job_id,
        lease_owner="worker-after-restart",
        kind=JobEventKind.SUCCEEDED,
        detail_code="completed",
        entity_ids=("paper:one",),
        artifact_ids=("artifact:report",),
    )
    assert completed.status is JobStatus.SUCCEEDED
    final_event = (await reopened.events(claim.record.job_id))[-1]
    assert final_event.entity_ids == ("paper:one",)
    assert final_event.artifact_ids == ("artifact:report",)


@pytest.mark.asyncio
async def test_session_events_have_one_durable_order_across_jobs(tmp_path) -> None:
    path, session, store = await job_store(tmp_path, ids=("job-1", "job-2"))
    first = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private first instruction",
    )
    await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-2",
        instruction="private second instruction",
    )
    await store.set_priority(
        principal_id="owner",
        job_id=first.record.job_id,
        priority=0,
        idempotency_key="priority-1",
    )

    events = await store.session_events(session.session_id)
    assert [event.delivery_sequence for event in events] == [1, 2, 3]
    assert [event.job_id for event in events] == ["job-1", "job-2", "job-1"]
    assert [
        event.delivery_sequence
        for event in await store.session_events(session.session_id, after_sequence=1)
    ] == [2, 3]
    assert "private" not in repr(events)

    reopened = JobStore(path)
    assert await reopened.session_events(session.session_id) == events


@pytest.mark.asyncio
async def test_notification_claim_and_final_disposition_are_restart_safe(tmp_path) -> None:
    path, session, store = await job_store(tmp_path)
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private instruction",
    )
    event = (await store.events(accepted.record.job_id))[0]

    claims = await asyncio.gather(
        *(
            store.claim_notification(
                session_id=session.session_id,
                event_id=event.event_id,
                claim_owner=f"media-{index}",
            )
            for index in range(8)
        )
    )
    assert sum(claims) == 1
    winner = f"media-{claims.index(True)}"
    assert await store.complete_notification(
        session_id=session.session_id,
        event_id=event.event_id,
        claim_owner=winner,
        disposition=JobNotificationDisposition.SPOKEN,
    )

    reopened = JobStore(path)
    assert (
        await reopened.notification_disposition(
            session_id=session.session_id,
            event_id=event.event_id,
        )
        is JobNotificationDisposition.SPOKEN
    )
    assert not await reopened.claim_notification(
        session_id=session.session_id,
        event_id=event.event_id,
        claim_owner="media-after-restart",
    )


@pytest.mark.asyncio
async def test_notification_claim_can_be_released_before_audio_starts(tmp_path) -> None:
    _path, session, store = await job_store(tmp_path)
    accepted = await store.accept(
        principal_id="owner",
        session_id=session.session_id,
        generation=1,
        idempotency_key="accept-1",
        instruction="private instruction",
    )
    event = (await store.events(accepted.record.job_id))[0]

    assert await store.claim_notification(
        session_id=session.session_id,
        event_id=event.event_id,
        claim_owner="media-one",
    )
    assert await store.release_notification(
        session_id=session.session_id,
        event_id=event.event_id,
        claim_owner="media-one",
    )
    assert await store.claim_notification(
        session_id=session.session_id,
        event_id=event.event_id,
        claim_owner="media-two",
    )
