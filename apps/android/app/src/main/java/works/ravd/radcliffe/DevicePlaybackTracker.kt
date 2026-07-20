package works.ravd.radcliffe

import com.google.protobuf.Timestamp
import works.ravd.radcliffe.protocol.v2.OperationRef
import works.ravd.radcliffe.protocol.v2.PlaybackAcknowledgement
import works.ravd.radcliffe.protocol.v2.PlaybackSegment
import works.ravd.radcliffe.protocol.v2.PlaybackStatus
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

class DevicePlaybackTracker(
    private val acknowledgementId: () -> String = { UUID.randomUUID().toString() },
    private val clockMs: () -> Long = System::currentTimeMillis,
) {
    private data class Pending(
        val segment: PlaybackSegment,
        var started: Boolean = false,
        var positionMs: Long = 0,
        var lastReportedPositionMs: Long = 0,
    )

    private val pending = ArrayDeque<Pending>()
    private val knownPlaybackIds = mutableSetOf<String>()
    private val invalidatedGenerationBySession = mutableMapOf<String, Long>()

    @Synchronized
    fun announce(segment: PlaybackSegment): Boolean {
        val operation = segment.operation
        if (
            segment.playbackId.isBlank() ||
            segment.segmentId.isBlank() ||
            segment.durationMs == 0L ||
            operation.sessionId.isBlank() ||
            operation.generation == 0L
        ) {
            return false
        }
        val invalidatedGeneration = invalidatedGenerationBySession[operation.sessionId] ?: 0L
        if (operation.generation <= invalidatedGeneration) return false
        if (!knownPlaybackIds.add(segment.playbackId)) return false
        pending.addLast(Pending(segment))
        return true
    }

    @Synchronized
    fun onAudioFrame(
        sampleRate: Int,
        numberOfFrames: Int,
        audible: Boolean,
        nowMs: Long = clockMs(),
    ): List<PlaybackAcknowledgement> {
        if (sampleRate <= 0 || numberOfFrames <= 0 || pending.isEmpty()) return emptyList()
        val active = pending.first()
        if (!active.started && !audible) return emptyList()
        val acknowledgements = mutableListOf<PlaybackAcknowledgement>()
        if (!active.started) {
            active.started = true
            acknowledgements += active.acknowledgement(
                PlaybackStatus.PLAYBACK_STATUS_STARTED,
                positionMs = 0,
                boundaryIndex = active.segment.boundaryIndex,
                nowMs = nowMs,
            )
        }
        val frameDurationMs = (numberOfFrames * 1000L / sampleRate).coerceAtLeast(1)
        active.positionMs = (active.positionMs + frameDurationMs).coerceAtMost(active.segment.durationMs)
        if (active.positionMs >= active.segment.durationMs) {
            acknowledgements += active.acknowledgement(
                PlaybackStatus.PLAYBACK_STATUS_COMPLETED,
                positionMs = active.segment.durationMs,
                boundaryIndex = active.segment.boundaryIndex + 1,
                nowMs = nowMs,
            )
            pending.removeFirst()
        } else if (active.positionMs - active.lastReportedPositionMs >= POSITION_INTERVAL_MS) {
            active.lastReportedPositionMs = active.positionMs
            acknowledgements += active.acknowledgement(
                PlaybackStatus.PLAYBACK_STATUS_POSITION,
                positionMs = active.positionMs,
                boundaryIndex = active.segment.boundaryIndex,
                nowMs = nowMs,
            )
        }
        return acknowledgements
    }

    @Synchronized
    fun flush(detail: String, nowMs: Long = clockMs()): List<PlaybackAcknowledgement> =
        finishPending(PlaybackStatus.PLAYBACK_STATUS_FLUSHED, detail, nowMs)

    @Synchronized
    fun invalidatePending(
        detail: String,
        nowMs: Long = clockMs(),
    ): List<PlaybackAcknowledgement> {
        pending.forEach { item ->
            val operation = item.segment.operation
            invalidatedGenerationBySession.merge(
                operation.sessionId,
                operation.generation,
                ::maxOf,
            )
        }
        return finishPending(PlaybackStatus.PLAYBACK_STATUS_FLUSHED, detail, nowMs)
    }

    @Synchronized
    fun invalidateThrough(
        operation: OperationRef,
        detail: String,
        nowMs: Long = clockMs(),
    ): List<PlaybackAcknowledgement> {
        if (operation.sessionId.isBlank() || operation.generation == 0L) return emptyList()
        invalidatedGenerationBySession.merge(
            operation.sessionId,
            operation.generation,
            ::maxOf,
        )
        return finishPending(
            PlaybackStatus.PLAYBACK_STATUS_FLUSHED,
            detail,
            nowMs,
        ) { item ->
            item.segment.operation.sessionId == operation.sessionId &&
                item.segment.operation.generation <= operation.generation
        }
    }

    @Synchronized
    fun fail(detail: String, nowMs: Long = clockMs()): List<PlaybackAcknowledgement> =
        finishPending(PlaybackStatus.PLAYBACK_STATUS_FAILED, detail, nowMs)

    @Synchronized
    fun pendingCount(): Int = pending.size

    private fun finishPending(
        status: PlaybackStatus,
        detail: String,
        nowMs: Long,
        matches: (Pending) -> Boolean = { true },
    ): List<PlaybackAcknowledgement> = buildList {
        val retained = ArrayDeque<Pending>()
        while (pending.isNotEmpty()) {
            val item = pending.removeFirst()
            if (matches(item)) {
                add(
                    item.acknowledgement(
                        status,
                        positionMs = item.positionMs,
                        boundaryIndex = item.segment.boundaryIndex,
                        nowMs = nowMs,
                        detail = detail,
                    ),
                )
            } else {
                retained.addLast(item)
            }
        }
        pending.addAll(retained)
    }

    private fun Pending.acknowledgement(
        status: PlaybackStatus,
        positionMs: Long,
        boundaryIndex: Int,
        nowMs: Long,
        detail: String = "",
    ): PlaybackAcknowledgement = PlaybackAcknowledgement.newBuilder()
        .setAcknowledgementId(acknowledgementId())
        .setPlaybackId(segment.playbackId)
        .setOperation(segment.operation)
        .setSegmentId(segment.segmentId)
        .setStatus(status)
        .setPositionMs(positionMs)
        .setBoundaryIndex(boundaryIndex)
        .setOccurredAt(nowMs.timestamp())
        .setDetail(detail)
        .build()

    companion object {
        private const val POSITION_INTERVAL_MS = 250L
        private const val AUDIBLE_PCM16_PEAK = 96

        fun isAudible(buffer: ByteBuffer, bitsPerSample: Int): Boolean {
            if (bitsPerSample != 16) return buffer.hasRemaining()
            val samples = buffer.duplicate().order(ByteOrder.LITTLE_ENDIAN)
            while (samples.remaining() >= Short.SIZE_BYTES) {
                if (kotlin.math.abs(samples.short.toInt()) >= AUDIBLE_PCM16_PEAK) return true
            }
            return false
        }

        private fun Long.timestamp(): Timestamp = Timestamp.newBuilder()
            .setSeconds(this / 1000)
            .setNanos(((this % 1000) * 1_000_000).toInt())
            .build()
    }
}
