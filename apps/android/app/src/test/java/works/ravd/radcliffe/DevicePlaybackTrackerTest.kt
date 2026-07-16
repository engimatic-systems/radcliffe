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

    private fun segment(durationMs: Long) = PlaybackSegment.newBuilder()
        .setPlaybackId("playback-1")
        .setOperation(
            OperationRef.newBuilder()
                .setSessionId("session-1")
                .setGeneration(1)
                .setOperationId("operation-1")
                .setCancellationId("cancel-1"),
        )
        .setSegmentId("source-1:3")
        .setBoundaryIndex(3)
        .setDurationMs(durationMs)
        .setSource(SourceKind.SOURCE_KIND_TEXT_SOURCE)
        .build()
}
