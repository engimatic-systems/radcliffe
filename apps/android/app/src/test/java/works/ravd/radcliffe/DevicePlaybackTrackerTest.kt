package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import works.ravd.radcliffe.protocol.v2.OperationRef
import works.ravd.radcliffe.protocol.v2.PlaybackSegment
import works.ravd.radcliffe.protocol.v2.PlaybackStatus
import works.ravd.radcliffe.protocol.v2.SourceKind
import java.nio.ByteBuffer
import java.nio.ByteOrder

class DevicePlaybackTrackerTest {
    @Test
    fun decodedAudioProducesStartedPositionAndCompletedBoundaries() {
        var id = 0
        val tracker = DevicePlaybackTracker(acknowledgementId = { "ack-${++id}" })
        assertTrue(tracker.announce(segment(durationMs = 500)))
        assertFalse(tracker.announce(segment(durationMs = 500)))

        assertTrue(tracker.onAudioFrame(48_000, 480, audible = false, nowMs = 100).isEmpty())
        val acknowledgements = buildList {
            repeat(50) { frame ->
                addAll(
                    tracker.onAudioFrame(
                        sampleRate = 48_000,
                        numberOfFrames = 480,
                        audible = frame == 0,
                        nowMs = 110L + frame * 10,
                    ),
                )
            }
        }

        assertEquals(
            listOf(
                PlaybackStatus.PLAYBACK_STATUS_STARTED,
                PlaybackStatus.PLAYBACK_STATUS_POSITION,
                PlaybackStatus.PLAYBACK_STATUS_COMPLETED,
            ),
            acknowledgements.map { it.status },
        )
        assertEquals(listOf(0L, 250L, 500L), acknowledgements.map { it.positionMs })
        assertEquals(listOf(3, 3, 4), acknowledgements.map { it.boundaryIndex })
        assertTrue(acknowledgements.all { it.playbackId == "playback-1" })
        assertTrue(acknowledgements.all { it.operation.traceId == "trc_${"a".repeat(32)}" })
        assertEquals(0, tracker.pendingCount())
    }

    @Test
    fun pauseFlushesMeasuredPartialPositionWithoutAdvancingBoundary() {
        val tracker = DevicePlaybackTracker(acknowledgementId = { "flush-ack" })
        tracker.announce(segment(durationMs = 1000))
        repeat(12) { frame ->
            tracker.onAudioFrame(48_000, 480, audible = frame == 0, nowMs = frame * 10L)
        }

        val flushed = tracker.flush("paused on device", nowMs = 150)

        assertEquals(1, flushed.size)
        assertEquals(PlaybackStatus.PLAYBACK_STATUS_FLUSHED, flushed[0].status)
        assertEquals(120L, flushed[0].positionMs)
        assertEquals(3, flushed[0].boundaryIndex)
        assertEquals("paused on device", flushed[0].detail)
        assertEquals(0, tracker.pendingCount())
    }

    @Test
    fun audibleDetectionReadsPcmWithoutMutatingTheCallbackBuffer() {
        val silent = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
        val audible = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(0)
            .putShort(120)
            .putShort(0)
            .putShort(0)
            .flip() as ByteBuffer
        silent.position(2)
        val originalPosition = silent.position()

        assertFalse(DevicePlaybackTracker.isAudible(silent, 16))
        assertEquals(originalPosition, silent.position())
        assertTrue(DevicePlaybackTracker.isAudible(audible, 16))
    }

    @Test
    fun verifiedInterruptionFlushesQueuedGenerationAndRejectsLateFrames() {
        var id = 0
        val tracker = DevicePlaybackTracker(acknowledgementId = { "interrupt-${++id}" })
        assertTrue(tracker.announce(segment(durationMs = 1000, playbackId = "current-1")))
        assertTrue(tracker.announce(segment(durationMs = 1000, playbackId = "current-2")))
        tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 10)

        val interrupted = operation(generation = 4)
        val flushed = tracker.invalidateThrough(
            interrupted,
            "verified speech interrupted playback",
            nowMs = 20,
        )

        assertEquals(2, flushed.size)
        assertEquals(listOf(10L, 0L), flushed.map { it.positionMs })
        assertTrue(flushed.all { it.status == PlaybackStatus.PLAYBACK_STATUS_FLUSHED })
        assertEquals(0, tracker.pendingCount())
        assertFalse(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "late-current", generation = 4),
            ),
        )
        assertTrue(tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 30).isEmpty())

        assertTrue(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "replacement", generation = 5),
            ),
        )
        assertEquals(
            PlaybackStatus.PLAYBACK_STATUS_STARTED,
            tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 40).single().status,
        )
    }

    @Test
    fun localInterruptInvalidatesEveryPendingGeneration() {
        val tracker = DevicePlaybackTracker(acknowledgementId = { "local-interrupt" })
        tracker.announce(segment(durationMs = 1000, playbackId = "generation-3", generation = 3))
        tracker.announce(segment(durationMs = 1000, playbackId = "generation-4", generation = 4))

        assertEquals(2, tracker.invalidatePending("interrupted on device").size)
        assertFalse(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "late-3", generation = 3),
            ),
        )
        assertFalse(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "late-4", generation = 4),
            ),
        )
        assertTrue(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "fresh-5", generation = 5),
            ),
        )
    }

    private fun operation(generation: Long = 1) = OperationRef.newBuilder()
        .setSessionId("session-1")
        .setGeneration(generation)
        .setOperationId("operation-$generation")
        .setCancellationId("cancel-$generation")
        .setTraceId("trc_${"a".repeat(32)}")
        .build()

    private fun segment(
        durationMs: Long,
        playbackId: String = "playback-1",
        generation: Long = 1,
    ) = PlaybackSegment.newBuilder()
        .setPlaybackId(playbackId)
        .setOperation(operation(generation))
        .setSegmentId("source-1:3")
        .setBoundaryIndex(3)
        .setDurationMs(durationMs)
        .setSource(SourceKind.SOURCE_KIND_TEXT_SOURCE)
        .build()
}
