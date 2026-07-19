from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Protocol

from loguru import logger
from radcliffe_domain.jobs import (
    JobEventKind,
    JobEventRecord,
    JobNotificationDisposition,
    JobNotificationPolicy,
    JobStore,
)

from radcliffe_media.pipeline import (
    JobAnnouncement,
    NotificationAnnouncementOutcome,
)


class JobEventSink(Protocol):
    async def job_event(
        self,
        record: JobEventRecord,
        participant_id: str | None = None,
    ) -> None: ...


class JobAnnouncementVoice(Protocol):
    def notification_foreground_available(self) -> bool: ...

    async def announce_job(
        self,
        event_id: str,
        announcement: JobAnnouncement,
        *,
        foreground_quiet: Callable[[], bool],
        claim: Callable[[], Awaitable[bool]],
        release: Callable[[], Awaitable[bool]],
    ) -> NotificationAnnouncementOutcome: ...


_ANNOUNCEMENTS = {
    (JobEventKind.WAITING, "waiting_for_user"): JobAnnouncement.WAITING_FOR_USER,
    (JobEventKind.WAITING, "waiting_for_resource"): JobAnnouncement.WAITING_FOR_RESOURCE,
    (JobEventKind.SUCCEEDED, "completed"): JobAnnouncement.COMPLETE,
    (JobEventKind.FAILED, "executor_failed"): JobAnnouncement.FAILED,
    (JobEventKind.FAILED, "budget_exhausted"): JobAnnouncement.FAILED,
    (JobEventKind.FAILED, "runtime_exhausted"): JobAnnouncement.FAILED,
    (JobEventKind.FAILED, "resource_exhausted"): JobAnnouncement.FAILED,
    (JobEventKind.FAILED, "policy_denied"): JobAnnouncement.FAILED,
    (JobEventKind.CANCELLED, "cancelled"): JobAnnouncement.CANCELLED,
    (JobEventKind.CANCELLED, "cancelled_by_user"): JobAnnouncement.CANCELLED,
    (JobEventKind.CANCELLED, "cancelled_by_timeout"): JobAnnouncement.CANCELLED,
    (JobEventKind.CANCELLED, "cancelled_by_operator"): JobAnnouncement.CANCELLED,
}

_POLICY_ORDER = {
    JobNotificationPolicy.IMMEDIATE: 0,
    JobNotificationPolicy.USER_WAITING: 1,
    JobNotificationPolicy.NEXT_SAFE_PAUSE: 2,
    JobNotificationPolicy.SILENT: 3,
}

_OUTCOME_TO_DISPOSITION = {
    NotificationAnnouncementOutcome.SPOKEN: JobNotificationDisposition.SPOKEN,
    NotificationAnnouncementOutcome.INTERRUPTED: JobNotificationDisposition.INTERRUPTED,
    NotificationAnnouncementOutcome.FAILED: JobNotificationDisposition.FAILED,
}


def announcement_for(event: JobEventRecord) -> JobAnnouncement | None:
    return _ANNOUNCEMENTS.get((event.kind, event.detail_code))


class JobNotificationScheduler:
    def __init__(
        self,
        *,
        store: JobStore,
        session_id: str,
        events: JobEventSink,
        voice: JobAnnouncementVoice,
        foreground_quiet: Callable[[], bool],
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("notification poll interval must be positive")
        self.store = store
        self.session_id = session_id
        self.events = events
        self.voice = voice
        self.foreground_quiet = foreground_quiet
        self.poll_interval_seconds = poll_interval_seconds
        self.claim_owner = (
            "media_notification_" + hashlib.sha256(session_id.encode()).hexdigest()[:24]
        )
        self._after_sequence = 0
        self._pending: dict[str, JobEventRecord] = {}
        self._poll_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="radcliffe-job-notifications",
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        self._stopping.set()
        self._wake.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def poll_once(self) -> None:
        async with self._poll_lock:
            while True:
                batch = await self.store.session_events(
                    self.session_id,
                    after_sequence=self._after_sequence,
                    limit=500,
                )
                if not batch:
                    break
                for event in batch:
                    await self.events.job_event(event)
                    self._pending[event.event_id] = event
                    self._after_sequence = event.delivery_sequence
                if len(batch) < 500:
                    break
            await self._deliver_pending()

    async def replay_visibility(self, participant_id: str) -> None:
        async with self._poll_lock:
            after_sequence = 0
            while True:
                batch = await self.store.session_events(
                    self.session_id,
                    after_sequence=after_sequence,
                    limit=500,
                )
                if not batch:
                    return
                for event in batch:
                    await self.events.job_event(event, participant_id)
                    after_sequence = event.delivery_sequence
                if len(batch) < 500:
                    return

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "background job notification poll failed type={}",
                    type(error).__name__,
                )
            if self._stopping.is_set():
                return
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _deliver_pending(self) -> None:
        ordered = sorted(
            self._pending.values(),
            key=lambda event: (
                _POLICY_ORDER[event.notification_policy],
                event.priority,
                event.delivery_sequence,
            ),
        )
        for event in ordered:
            disposition = await self.store.notification_disposition(
                session_id=self.session_id,
                event_id=event.event_id,
            )
            if disposition is not None:
                self._pending.pop(event.event_id, None)
                continue
            if event.notification_policy is JobNotificationPolicy.SILENT:
                await self.store.suppress_notification(
                    session_id=self.session_id,
                    event_id=event.event_id,
                    disposition=JobNotificationDisposition.SILENT,
                )
                self._pending.pop(event.event_id, None)
                continue
            announcement = announcement_for(event)
            if announcement is None:
                await self.store.suppress_notification(
                    session_id=self.session_id,
                    event_id=event.event_id,
                    disposition=JobNotificationDisposition.VISIBILITY_ONLY,
                )
                self._pending.pop(event.event_id, None)
                continue
            if not self.foreground_quiet() or not self.voice.notification_foreground_available():
                continue

            event_id = event.event_id

            async def claim(event_id: str = event_id) -> bool:
                return await self.store.claim_notification(
                    session_id=self.session_id,
                    event_id=event_id,
                    claim_owner=self.claim_owner,
                )

            async def release(event_id: str = event_id) -> bool:
                return await self.store.release_notification(
                    session_id=self.session_id,
                    event_id=event_id,
                    claim_owner=self.claim_owner,
                )

            announcement_task = asyncio.create_task(
                self.voice.announce_job(
                    event.event_id,
                    announcement,
                    foreground_quiet=self.foreground_quiet,
                    claim=claim,
                    release=release,
                ),
                name=f"radcliffe-job-announcement-{event.delivery_sequence}",
            )
            outcome = await announcement_task
            if outcome is NotificationAnnouncementOutcome.DEFERRED:
                continue
            if outcome is NotificationAnnouncementOutcome.ALREADY_DELIVERED:
                self._pending.pop(event.event_id, None)
                continue
            await self.store.complete_notification(
                session_id=self.session_id,
                event_id=event.event_id,
                claim_owner=self.claim_owner,
                disposition=_OUTCOME_TO_DISPOSITION[outcome],
            )
            self._pending.pop(event.event_id, None)
