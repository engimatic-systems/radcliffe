import asyncio
from collections.abc import Awaitable, Callable

import pytest
from radcliffe_domain.catalog import SessionCatalog
from radcliffe_domain.jobs import (
    JobEventKind,
    JobEventRecord,
    JobNotificationDisposition,
    JobNotificationPolicy,
    JobStore,
)
from radcliffe_domain.store import EventStore
from radcliffe_media.notifications import JobNotificationScheduler
from radcliffe_media.pipeline import (
    JobAnnouncement,
    NotificationAnnouncementOutcome,
    VoicePipeline,
)


class FakeJobEvents:
    def __init__(self) -> None:
        self.sent: list[tuple[JobEventRecord, str | None]] = []

    async def job_event(
        self,
        record: JobEventRecord,
        participant_id: str | None = None,
    ) -> None:
        self.sent.append((record, participant_id))


class FakeJobVoice:
    def __init__(self, quiet: list[bool]) -> None:
        self.quiet = quiet
        self.available = True
        self.defer_after_claim = False
        self.outcome = NotificationAnnouncementOutcome.SPOKEN
        self.announcements: list[tuple[str, JobAnnouncement]] = []

    def notification_foreground_available(self) -> bool:
        return self.available

    async def announce_job(
        self,
        event_id: str,
        announcement: JobAnnouncement,
        *,
        job_id: str,
        status: str,
        detail_code: str,
        foreground_quiet: Callable[[], bool],
        claim: Callable[[], Awaitable[bool]],
        release: Callable[[], Awaitable[bool]],
    ) -> NotificationAnnouncementOutcome:
        if not self.available or not foreground_quiet():
            return NotificationAnnouncementOutcome.DEFERRED
        if not await claim():
            return NotificationAnnouncementOutcome.ALREADY_DELIVERED
        if self.defer_after_claim:
            self.defer_after_claim = False
            self.quiet[0] = False
        if not foreground_quiet():
            await release()
            return NotificationAnnouncementOutcome.DEFERRED
        del job_id, status, detail_code
        self.announcements.append((event_id, announcement))
        return self.outcome


class FakeSynthesizer:
    supports_speech_rate = True

    async def synthesize(self, canonical_text: str, *, rate: float = 1.0) -> bytes:
        del rate
        return canonical_text.encode()


class BlockingPublisher:
    def __init__(self) -> None:
        self.published = asyncio.Event()
        self.flushed = False

    @staticmethod
    def duration_ms(pcm_audio: bytes) -> int:
        return len(pcm_audio)

    async def publish(
        self,
        _segment_id: str,
        pcm_audio: bytes,
        *,
        wait_for_playback: bool = False,
    ) -> int:
        del wait_for_playback
        self.published.set()
        await asyncio.Event().wait()
        return len(pcm_audio)

    async def flush(self) -> None:
        self.flushed = True


class UnusedOrchestrator:
    async def turn(self, request, cancelled=None):
        del request, cancelled
        if False:
            yield


async def notification_store(tmp_path, *, ids=("job-1", "job-2")):
    path = tmp_path / "notifications.sqlite3"
    await EventStore(path).migrate()
    session, _created = await SessionCatalog(path).create(
        principal_id="owner",
        session_id="session-1",
    )
    identifiers = iter(ids)
    return path, session, JobStore(path, id_factory=lambda: next(identifiers))


async def complete_job(
    store: JobStore,
    *,
    session_id: str,
    key: str,
    policy: JobNotificationPolicy,
    priority: int = 2,
) -> JobEventRecord:
    accepted = await store.accept(
        principal_id="owner",
        session_id=session_id,
        generation=1,
        idempotency_key=key,
        instruction=f"private instruction for {key}",
        notification_policy=policy,
        priority=priority,
    )
    claim = await store.claim_next(lease_owner=f"executor-{key}")
    assert claim is not None
    assert claim.record.job_id == accepted.record.job_id
    await store.publish(
        job_id=accepted.record.job_id,
        lease_owner=f"executor-{key}",
        kind=JobEventKind.SUCCEEDED,
        detail_code="completed",
    )
    return (await store.events(accepted.record.job_id))[-1]


def scheduler(
    store: JobStore,
    events: FakeJobEvents,
    voice: FakeJobVoice,
    quiet: list[bool],
) -> JobNotificationScheduler:
    return JobNotificationScheduler(
        store=store,
        session_id="session-1",
        events=events,
        voice=voice,
        foreground_quiet=lambda: quiet[0],
    )


@pytest.mark.asyncio
async def test_visibility_is_immediate_but_speech_waits_for_a_safe_pause(tmp_path) -> None:
    _path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="first",
        policy=JobNotificationPolicy.IMMEDIATE,
    )
    quiet = [False]
    events = FakeJobEvents()
    voice = FakeJobVoice(quiet)
    notifications = scheduler(store, events, voice, quiet)

    await notifications.poll_once()

    assert events.sent[-1][0] == completed
    assert voice.announcements == []
    assert "private instruction" not in repr(events.sent)
    assert (
        await store.notification_disposition(
            session_id=session.session_id,
            event_id=completed.event_id,
        )
        is None
    )

    quiet[0] = True
    await notifications.poll_once()

    assert voice.announcements == [(completed.event_id, JobAnnouncement.COMPLETE)]
    assert (
        await store.notification_disposition(
            session_id=session.session_id,
            event_id=completed.event_id,
        )
        is JobNotificationDisposition.SPOKEN
    )


@pytest.mark.asyncio
async def test_silent_policy_is_visible_and_never_speaks(tmp_path) -> None:
    _path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="silent",
        policy=JobNotificationPolicy.SILENT,
    )
    quiet = [True]
    events = FakeJobEvents()
    voice = FakeJobVoice(quiet)

    await scheduler(store, events, voice, quiet).poll_once()

    assert completed.event_id in [event.event_id for event, _participant in events.sent]
    assert voice.announcements == []
    assert (
        await store.notification_disposition(
            session_id=session.session_id,
            event_id=completed.event_id,
        )
        is JobNotificationDisposition.SILENT
    )


@pytest.mark.asyncio
async def test_safety_change_after_claim_releases_it_for_the_next_pause(tmp_path) -> None:
    _path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="race",
        policy=JobNotificationPolicy.NEXT_SAFE_PAUSE,
    )
    quiet = [True]
    events = FakeJobEvents()
    voice = FakeJobVoice(quiet)
    voice.defer_after_claim = True
    notifications = scheduler(store, events, voice, quiet)

    await notifications.poll_once()
    assert voice.announcements == []
    assert (
        await store.notification_disposition(
            session_id=session.session_id,
            event_id=completed.event_id,
        )
        is None
    )

    quiet[0] = True
    await notifications.poll_once()
    assert voice.announcements == [(completed.event_id, JobAnnouncement.COMPLETE)]


@pytest.mark.asyncio
async def test_restart_and_reconnect_replay_visibility_without_duplicate_speech(tmp_path) -> None:
    path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="restart",
        policy=JobNotificationPolicy.USER_WAITING,
    )
    quiet = [True]
    first_events = FakeJobEvents()
    first_voice = FakeJobVoice(quiet)
    await scheduler(store, first_events, first_voice, quiet).poll_once()
    assert first_voice.announcements == [(completed.event_id, JobAnnouncement.COMPLETE)]

    reopened = JobStore(path)
    second_events = FakeJobEvents()
    second_voice = FakeJobVoice(quiet)
    restarted = scheduler(reopened, second_events, second_voice, quiet)
    await restarted.poll_once()
    await restarted.replay_visibility("participant-after-reconnect")

    assert second_voice.announcements == []
    assert [event.delivery_sequence for event, target in second_events.sent if target is None] == [
        1,
        2,
        3,
    ]
    assert [
        event.delivery_sequence
        for event, target in second_events.sent
        if target == "participant-after-reconnect"
    ] == [1, 2, 3]


@pytest.mark.asyncio
async def test_simultaneous_jobs_announce_by_policy_priority_then_sequence(tmp_path) -> None:
    _path, session, store = await notification_store(tmp_path)
    low = await complete_job(
        store,
        session_id=session.session_id,
        key="low",
        policy=JobNotificationPolicy.NEXT_SAFE_PAUSE,
        priority=3,
    )
    high = await complete_job(
        store,
        session_id=session.session_id,
        key="high",
        policy=JobNotificationPolicy.IMMEDIATE,
        priority=0,
    )
    quiet = [True]
    events = FakeJobEvents()
    voice = FakeJobVoice(quiet)

    await scheduler(store, events, voice, quiet).poll_once()

    assert [event_id for event_id, _announcement in voice.announcements] == [
        high.event_id,
        low.event_id,
    ]


@pytest.mark.asyncio
async def test_interrupted_announcement_is_not_repeated_after_restart(tmp_path) -> None:
    path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="interrupted",
        policy=JobNotificationPolicy.IMMEDIATE,
    )
    quiet = [True]
    events = FakeJobEvents()
    voice = FakeJobVoice(quiet)
    voice.outcome = NotificationAnnouncementOutcome.INTERRUPTED
    await scheduler(store, events, voice, quiet).poll_once()

    assert (
        await store.notification_disposition(
            session_id=session.session_id,
            event_id=completed.event_id,
        )
        is JobNotificationDisposition.INTERRUPTED
    )
    restarted_voice = FakeJobVoice(quiet)
    await scheduler(JobStore(path), FakeJobEvents(), restarted_voice, quiet).poll_once()
    assert restarted_voice.announcements == []


@pytest.mark.asyncio
async def test_scheduler_survives_barge_in_while_its_child_owns_audio(tmp_path) -> None:
    path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="live-interruption",
        policy=JobNotificationPolicy.IMMEDIATE,
    )
    publisher = BlockingPublisher()
    voice = VoicePipeline(
        session.session_id,
        EventStore(path),
        UnusedOrchestrator(),
        FakeSynthesizer(),
        publisher,
    )
    await voice.start()
    notifications = JobNotificationScheduler(
        store=store,
        session_id=session.session_id,
        events=FakeJobEvents(),
        voice=voice,
        foreground_quiet=lambda: True,
    )
    poll = asyncio.create_task(notifications.poll_once())
    await asyncio.wait_for(publisher.published.wait(), timeout=1)

    await asyncio.wait_for(voice.barge_in(), timeout=1)
    await asyncio.wait_for(poll, timeout=1)

    assert publisher.flushed
    assert (
        await store.notification_disposition(
            session_id=session.session_id,
            event_id=completed.event_id,
        )
        is JobNotificationDisposition.INTERRUPTED
    )
    assert voice.notification_foreground_available()


@pytest.mark.asyncio
async def test_scheduler_stop_cannot_hang_on_an_active_announcement(tmp_path) -> None:
    path, session, store = await notification_store(tmp_path)
    completed = await complete_job(
        store,
        session_id=session.session_id,
        key="shutdown",
        policy=JobNotificationPolicy.IMMEDIATE,
    )
    publisher = BlockingPublisher()
    voice = VoicePipeline(
        session.session_id,
        EventStore(path),
        UnusedOrchestrator(),
        FakeSynthesizer(),
        publisher,
    )
    await voice.start()
    notifications = JobNotificationScheduler(
        store=store,
        session_id=session.session_id,
        events=FakeJobEvents(),
        voice=voice,
        foreground_quiet=lambda: True,
        poll_interval_seconds=60,
    )
    await notifications.start()
    await asyncio.wait_for(publisher.published.wait(), timeout=1)

    await asyncio.wait_for(notifications.stop(), timeout=1)

    assert await store.notification_disposition(
        session_id=session.session_id,
        event_id=completed.event_id,
    ) in {
        JobNotificationDisposition.ANNOUNCING,
        JobNotificationDisposition.INTERRUPTED,
    }
