package works.ravd.radcliffe

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import works.ravd.radcliffe.protocol.v2.JobEvent
import works.ravd.radcliffe.protocol.v2.JobEventKind
import works.ravd.radcliffe.protocol.v2.JobNotificationPolicy
import works.ravd.radcliffe.protocol.v2.JobRef
import works.ravd.radcliffe.protocol.v2.JobStatus

class BackgroundJobsTest {
    private fun job(
        id: String,
        sessionId: String = "session-1",
        revision: Long = 1,
        deliverySequence: Long = revision,
        status: BackgroundJobStatus = BackgroundJobStatus.QUEUED,
        priority: Int = 2,
        detailCode: String? = null,
    ): BackgroundJobSummary = BackgroundJobSummary(
        jobId = id,
        sessionId = sessionId,
        generation = 1,
        status = status,
        notificationPolicy = "next_safe_pause",
        priority = priority,
        budgetUsd = null,
        maxRuntimeSeconds = 3600,
        revision = revision,
        cancelRequested = false,
        attempt = 0,
        createdAt = Instant.parse("2026-07-18T12:00:00Z"),
        updatedAt = Instant.parse("2026-07-18T12:00:0${revision}Z"),
        eventId = "event-$id-$revision",
        deliverySequence = deliverySequence,
        detailCode = detailCode,
    )

    @Test
    fun replayAndStaleEventsCannotMoveAJobBackwards() {
        BackgroundJobModel.open("session-1")
        val running = job(
            "job-1",
            revision = 2,
            deliverySequence = 5,
            status = BackgroundJobStatus.RUNNING,
            detailCode = "searching",
        )

        assertTrue(BackgroundJobModel.update(running))
        assertFalse(BackgroundJobModel.update(running))
        assertFalse(BackgroundJobModel.update(job("job-1", revision = 1)))

        assertEquals(2L, BackgroundJobModel.jobs.value.single().revision)
        assertEquals("Searching", backgroundJobStatusLabel(BackgroundJobModel.jobs.value.single()))
    }

    @Test
    fun wrongSessionEventsAreIgnoredAndHandoffClearsJobs() {
        BackgroundJobModel.open("session-1")

        assertFalse(BackgroundJobModel.update(job("wrong", sessionId = "session-2")))
        assertTrue(BackgroundJobModel.update(job("right")))

        SessionModel.open("session-2", emptyList())

        assertTrue(BackgroundJobModel.jobs.value.isEmpty())
    }

    @Test
    fun simultaneousJobsSortActivePriorityBeforeTerminalHistory() {
        BackgroundJobModel.open("session-1")
        BackgroundJobModel.update(
            job("terminal", status = BackgroundJobStatus.SUCCEEDED, priority = 0),
        )
        BackgroundJobModel.update(job("low", priority = 4))
        BackgroundJobModel.update(job("high", priority = 0))

        assertEquals(
            listOf("high", "low", "terminal"),
            BackgroundJobModel.jobs.value.map { it.jobId },
        )
    }

    @Test
    fun compactSummaryCoversOneAndSeveralJobsWithoutExposingIdentifiers() {
        val one = backgroundJobsSummaryModel(listOf(job("private-job-id")))
        val several = backgroundJobsSummaryModel(
            listOf(
                job("running", status = BackgroundJobStatus.RUNNING),
                job("waiting", status = BackgroundJobStatus.WAITING),
                job("done", status = BackgroundJobStatus.SUCCEEDED),
            ),
        )

        assertEquals("1 active job", one.visibleLabel)
        assertEquals("2 active jobs · 1 needs attention · 1 completed", several.visibleLabel)
        assertTrue(several.accessibilityLabel.startsWith("Background jobs"))
        assertFalse(one.accessibilityLabel.contains("private-job-id"))
    }

    @Test
    fun olderApiHydrationDoesNotOverwriteNewerLiveEvent() {
        BackgroundJobModel.open("session-1")
        BackgroundJobModel.update(
            job("job-1", revision = 3, status = BackgroundJobStatus.WAITING),
        )

        BackgroundJobModel.hydrate("session-1", listOf(job("job-1", revision = 2)))

        assertEquals(BackgroundJobStatus.WAITING, BackgroundJobModel.jobs.value.single().status)
    }

    @Test
    fun openingTheHydratedSessionDoesNotClearItsJobs() {
        BackgroundJobModel.open("session-1")
        BackgroundJobModel.hydrate("session-1", listOf(job("job-1")))

        SessionModel.open("session-1", emptyList())

        assertEquals(listOf("job-1"), BackgroundJobModel.jobs.value.map { it.jobId })
    }

    @Test
    fun protocolEventValidationRejectsWrongSessionAndMapsBoundedMetadata() {
        val event = JobEvent.newBuilder()
            .setSchemaVersion(1)
            .setEventId("job-event:job-1:4")
            .setJob(
                JobRef.newBuilder()
                    .setJobId("job-1")
                    .setSessionId("session-1")
                    .setRevision(4)
                    .setStatus(JobStatus.JOB_STATUS_WAITING),
            )
            .setKind(JobEventKind.JOB_EVENT_KIND_WAITING)
            .setNotificationPolicy(
                JobNotificationPolicy.JOB_NOTIFICATION_POLICY_NEXT_SAFE_PAUSE,
            )
            .setPriority(1)
            .setDetailCode("waiting_for_user")
            .setDeliverySequence(9)
            .setOccurredAt(
                com.google.protobuf.Timestamp.newBuilder().setSeconds(1_752_840_000),
            )
            .addEntityIds("paper:one")
            .build()

        assertEquals(null, backgroundJobFromEvent(event, "session-2"))
        assertEquals(null, backgroundJobFromEvent(event.toBuilder().setKindValue(99).build(), "session-1"))
        val mapped = backgroundJobFromEvent(event, "session-1")

        assertEquals(BackgroundJobStatus.WAITING, mapped?.status)
        assertEquals("waiting_for_user", mapped?.detailCode)
        assertEquals(listOf("paper:one"), mapped?.entityIds)
        assertEquals(9L, mapped?.deliverySequence)
    }

    @Test
    fun terminalAndCancellationStateDisableInvalidControls() {
        val terminal = backgroundJobCardModel(
            job("done", status = BackgroundJobStatus.SUCCEEDED),
        )
        val cancelling = backgroundJobCardModel(job("cancel").copy(cancelRequested = true))

        assertFalse(terminal.controlsEnabled)
        assertFalse(cancelling.controlsEnabled)
        assertTrue(terminal.accessibilityLabel.contains("Complete"))
    }

    @Test
    fun cardLabelsCoverWaitingCompletionAndFailureWithoutRawDetail() {
        val waiting = backgroundJobCardModel(
            job("waiting", status = BackgroundJobStatus.WAITING, detailCode = "waiting_for_user"),
        )
        val complete = backgroundJobCardModel(
            job("complete", status = BackgroundJobStatus.SUCCEEDED, detailCode = "completed"),
        )
        val failed = backgroundJobCardModel(
            job("failed", status = BackgroundJobStatus.FAILED, detailCode = "executor_failed"),
        )
        val unknown = backgroundJobStatusLabel(
            job("future", status = BackgroundJobStatus.RUNNING, detailCode = "private_output_fragment"),
        )

        assertEquals("Needs your input", waiting.statusLabel)
        assertEquals("Complete", complete.statusLabel)
        assertEquals("Failed", failed.statusLabel)
        assertEquals("Updated", unknown)
    }
}
