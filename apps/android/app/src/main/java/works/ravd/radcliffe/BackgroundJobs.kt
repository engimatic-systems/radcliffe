package works.ravd.radcliffe

import java.time.Instant
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import works.ravd.radcliffe.protocol.v2.JobEvent
import works.ravd.radcliffe.protocol.v2.JobEventKind
import works.ravd.radcliffe.protocol.v2.JobNotificationPolicy
import works.ravd.radcliffe.protocol.v2.JobStatus

enum class BackgroundJobStatus {
    QUEUED,
    RUNNING,
    WAITING,
    SUCCEEDED,
    FAILED,
    CANCELLED,
    ;

    val terminal: Boolean
        get() = this in setOf(SUCCEEDED, FAILED, CANCELLED)

    companion object {
        fun parse(value: String): BackgroundJobStatus = valueOf(value.uppercase())
    }
}

data class BackgroundJobSummary(
    val jobId: String,
    val sessionId: String,
    val generation: Long,
    val status: BackgroundJobStatus,
    val notificationPolicy: String,
    val priority: Int,
    val budgetUsd: Double?,
    val maxRuntimeSeconds: Int,
    val revision: Long,
    val cancelRequested: Boolean,
    val attempt: Int,
    val createdAt: Instant,
    val updatedAt: Instant,
    val eventId: String? = null,
    val deliverySequence: Long = 0,
    val detailCode: String? = null,
    val entityIds: List<String> = emptyList(),
    val artifactIds: List<String> = emptyList(),
)

object BackgroundJobModel {
    private var activeSessionId: String = ""
    private var byId: Map<String, BackgroundJobSummary> = emptyMap()
    private val mutableJobs = MutableStateFlow<List<BackgroundJobSummary>>(emptyList())
    val jobs = mutableJobs.asStateFlow()

    fun open(sessionId: String, reset: Boolean = true) {
        if (!reset && activeSessionId == sessionId) return
        activeSessionId = sessionId
        byId = emptyMap()
        mutableJobs.value = emptyList()
    }

    fun hydrate(sessionId: String, values: List<BackgroundJobSummary>) {
        if (sessionId.isBlank() || sessionId != activeSessionId) return
        val next = byId.toMutableMap()
        values.filter { it.validFor(sessionId) }.forEach { incoming ->
            val current = next[incoming.jobId]
            if (current == null || incoming.revision > current.revision) {
                next[incoming.jobId] = incoming
            }
        }
        replace(next)
    }

    fun update(value: BackgroundJobSummary): Boolean {
        if (
            activeSessionId.isBlank() ||
            !value.validFor(activeSessionId)
        ) {
            return false
        }
        val current = byId[value.jobId]
        if (current != null) {
            if (value.revision < current.revision) return false
            if (
                value.revision == current.revision &&
                value.deliverySequence <= current.deliverySequence
            ) {
                return false
            }
        }
        replace(byId + (value.jobId to value))
        return true
    }

    private fun replace(values: Map<String, BackgroundJobSummary>) {
        byId = values
        mutableJobs.value = values.values.sortedWith(
            compareBy<BackgroundJobSummary> { it.status.terminal }
                .thenBy { it.priority }
                .thenByDescending { it.updatedAt }
                .thenBy { it.jobId },
        )
    }
}

private fun BackgroundJobSummary.validFor(sessionId: String): Boolean =
    sessionId.isNotBlank() &&
        this.sessionId == sessionId &&
        jobId.matches(Regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")) &&
        generation >= 0 &&
        revision > 0 &&
        priority in 0..4 &&
        (eventId != null || generation > 0 && maxRuntimeSeconds in 1..86_400) &&
        (eventId == null || deliverySequence > 0) &&
        attempt >= 0 &&
        createdAt <= updatedAt &&
        notificationPolicy in setOf("immediate", "next_safe_pause", "silent", "user_waiting")

internal fun backgroundJobStatusLabel(job: BackgroundJobSummary): String = when (job.status) {
    BackgroundJobStatus.QUEUED -> "Queued"
    BackgroundJobStatus.RUNNING -> job.detailCode?.let(::backgroundJobDetailLabel) ?: "Running"
    BackgroundJobStatus.WAITING -> job.detailCode?.let(::backgroundJobDetailLabel) ?: "Waiting"
    BackgroundJobStatus.SUCCEEDED -> "Complete"
    BackgroundJobStatus.FAILED -> "Failed"
    BackgroundJobStatus.CANCELLED -> "Cancelled"
}

internal fun backgroundJobDetailLabel(detailCode: String): String = when (detailCode) {
    "searching" -> "Searching"
    "downloading" -> "Downloading"
    "analyzing" -> "Analyzing"
    "coding" -> "Coding"
    "testing" -> "Testing"
    "compacting" -> "Compacting"
    "waiting_for_user" -> "Needs your input"
    "waiting_for_resource" -> "Waiting for a resource"
    "budget_exhausted" -> "Budget exhausted"
    "runtime_exhausted" -> "Runtime limit reached"
    "resource_exhausted" -> "Resource limit reached"
    "policy_denied" -> "Blocked by policy"
    else -> "Updated"
}

internal fun backgroundJobFromEvent(
    event: JobEvent,
    activeSessionId: String,
): BackgroundJobSummary? {
    val referencePattern = Regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    val detailPattern = Regex("^[a-z][a-z0-9_.-]{0,63}$")
    if (
        event.schemaVersion != 1 ||
        activeSessionId.isBlank() ||
        event.job.sessionId != activeSessionId ||
        !referencePattern.matches(event.eventId) ||
        !referencePattern.matches(event.job.jobId) ||
        event.job.revision <= 0 ||
        event.deliverySequence <= 0 ||
        event.priority !in 0..4 ||
        !event.hasOccurredAt() ||
        !detailPattern.matches(event.detailCode) ||
        event.entityIdsCount > 50 ||
        event.artifactIdsCount > 50 ||
        event.entityIdsList.any { !referencePattern.matches(it) } ||
        event.artifactIdsList.any { !referencePattern.matches(it) }
    ) {
        return null
    }
    val status = when (event.job.status) {
        JobStatus.JOB_STATUS_QUEUED -> BackgroundJobStatus.QUEUED
        JobStatus.JOB_STATUS_RUNNING -> BackgroundJobStatus.RUNNING
        JobStatus.JOB_STATUS_WAITING -> BackgroundJobStatus.WAITING
        JobStatus.JOB_STATUS_SUCCEEDED -> BackgroundJobStatus.SUCCEEDED
        JobStatus.JOB_STATUS_FAILED -> BackgroundJobStatus.FAILED
        JobStatus.JOB_STATUS_CANCELLED -> BackgroundJobStatus.CANCELLED
        else -> return null
    }
    val policy = when (event.notificationPolicy) {
        JobNotificationPolicy.JOB_NOTIFICATION_POLICY_IMMEDIATE -> "immediate"
        JobNotificationPolicy.JOB_NOTIFICATION_POLICY_NEXT_SAFE_PAUSE -> "next_safe_pause"
        JobNotificationPolicy.JOB_NOTIFICATION_POLICY_SILENT -> "silent"
        JobNotificationPolicy.JOB_NOTIFICATION_POLICY_USER_WAITING -> "user_waiting"
        else -> return null
    }
    if (
        event.kind == JobEventKind.JOB_EVENT_KIND_UNSPECIFIED ||
        event.kind == JobEventKind.UNRECOGNIZED
    ) {
        return null
    }
    val occurredAt = runCatching {
        Instant.ofEpochSecond(event.occurredAt.seconds, event.occurredAt.nanos.toLong())
    }.getOrNull() ?: return null
    return BackgroundJobSummary(
        jobId = event.job.jobId,
        sessionId = event.job.sessionId,
        generation = 0,
        status = status,
        notificationPolicy = policy,
        priority = event.priority,
        budgetUsd = null,
        maxRuntimeSeconds = 0,
        revision = event.job.revision,
        cancelRequested = event.kind == JobEventKind.JOB_EVENT_KIND_CANCELLATION_REQUESTED,
        attempt = 0,
        createdAt = occurredAt,
        updatedAt = occurredAt,
        eventId = event.eventId,
        deliverySequence = event.deliverySequence,
        detailCode = event.detailCode,
        entityIds = event.entityIdsList,
        artifactIds = event.artifactIdsList,
    )
}

data class BackgroundJobCardModel(
    val statusLabel: String,
    val priorityLabel: String,
    val controlsEnabled: Boolean,
    val accessibilityLabel: String,
)

internal fun backgroundJobCardModel(job: BackgroundJobSummary): BackgroundJobCardModel {
    val status = backgroundJobStatusLabel(job)
    val priority = when (job.priority) {
        0 -> "Urgent"
        1 -> "High"
        2 -> "Normal"
        3 -> "Low"
        else -> "Background"
    }
    val references = buildList {
        if (job.entityIds.isNotEmpty()) add("${job.entityIds.size} results")
        if (job.artifactIds.isNotEmpty()) add("${job.artifactIds.size} artifacts")
    }
    val suffix = references.joinToString(", ").let { if (it.isBlank()) "" else ", $it" }
    return BackgroundJobCardModel(
        statusLabel = status,
        priorityLabel = priority,
        controlsEnabled = !job.status.terminal && !job.cancelRequested,
        accessibilityLabel = "Background job, $status, $priority priority$suffix",
    )
}
