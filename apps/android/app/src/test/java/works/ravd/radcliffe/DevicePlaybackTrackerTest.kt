package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
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
        tracker.configureReceiverCredit("receiver-1", 1)
        val accepted = tracker.announce(segment(durationMs = 500))
        assertEquals(PlaybackStatus.PLAYBACK_STATUS_ACCEPTED, accepted?.status)
        assertEquals(accepted, tracker.announce(segment(durationMs = 500)))

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
        tracker.configureReceiverCredit("receiver-1", 1)
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
        tracker.configureReceiverCredit("receiver-1", 1)
        assertNotNull(tracker.announce(segment(durationMs = 1000, playbackId = "current-1")))
        assertNull(tracker.announce(segment(durationMs = 1000, playbackId = "current-2")))
        tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 10)

        val interrupted = operation(generation = 4)
        val flushed = tracker.invalidateThrough(
            interrupted,
            "verified speech interrupted playback",
            nowMs = 20,
        )

        assertEquals(1, flushed.size)
        assertEquals(listOf(10L), flushed.map { it.positionMs })
        assertTrue(flushed.all { it.status == PlaybackStatus.PLAYBACK_STATUS_FLUSHED })
        assertEquals(0, tracker.pendingCount())
        assertNull(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "late-current", generation = 4),
            ),
        )
        assertTrue(tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 30).isEmpty())

        assertNotNull(
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
        tracker.configureReceiverCredit("receiver-1", 1)
        tracker.announce(segment(durationMs = 1000, playbackId = "generation-3", generation = 3))

        assertEquals(1, tracker.invalidatePending("interrupted on device").size)
        assertNull(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "late-3", generation = 3),
            ),
        )
        assertNotNull(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "fresh-5", generation = 5),
            ),
        )
    }

    @Test
    fun reconnectSuspendsUncertainAttemptAndRetainsLatestPhysicalEvidence() {
        var id = 0
        val tracker = DevicePlaybackTracker(acknowledgementId = { "reconnect-${++id}" })
        tracker.configureReceiverCredit("receiver-1", 1)
        assertNotNull(tracker.announce(segment(durationMs = 1000, playbackId = "old-attempt")))
        tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 10)
        repeat(25) { frame ->
            tracker.onAudioFrame(48_000, 480, audible = true, nowMs = 20L + frame * 10)
        }

        tracker.suspendForReconnect()

        assertEquals(0, tracker.pendingCount())
        val snapshot = tracker.reconnectSnapshot().single()
        assertEquals("old-attempt", snapshot.playbackId)
        assertEquals(PlaybackStatus.PLAYBACK_STATUS_POSITION, snapshot.status)
        assertEquals(250L, snapshot.positionMs)
        tracker.configureReceiverCredit("receiver-1", 2)
        assertNull(
            tracker.announce(
                segment(durationMs = 1000, playbackId = "stale-new-attempt"),
            ),
        )
        assertNotNull(
            tracker.announce(
                segment(
                    durationMs = 1000,
                    playbackId = "replacement-attempt",
                    creditSequence = 2,
                    attemptSequence = 2,
                ),
            ),
        )
        assertEquals(
            snapshot,
            tracker.announce(
                segment(durationMs = 1000, playbackId = "old-attempt"),
            ),
        )
    }

    @Test
    fun processRestartReplaysThePriorTerminalAcknowledgementForADuplicateAttempt() {
        val saved = mutableListOf<List<works.ravd.radcliffe.protocol.v2.PlaybackAcknowledgement>>()
        val first = DevicePlaybackTracker(
            acknowledgementId = { "durable-ack-${saved.size}" },
            snapshotSink = saved::add,
        )
        first.configureReceiverCredit("receiver-before-restart", 1)
        val original = segment(durationMs = 10)
            .toBuilder()
            .setReceiverEpoch("receiver-before-restart")
            .build()
        first.announce(original)
        first.onAudioFrame(48_000, 480, audible = true, nowMs = 10)
        assertEquals(PlaybackStatus.PLAYBACK_STATUS_COMPLETED, saved.last().single().status)

        val restarted = DevicePlaybackTracker(initialAcknowledgements = saved.last())
        restarted.configureReceiverCredit("receiver-after-restart", 1)

        assertEquals(saved.last().single(), restarted.announce(original))
        assertEquals(0, restarted.pendingCount())
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
        creditSequence: Long = 1,
        attemptSequence: Long = 1,
    ) = PlaybackSegment.newBuilder()
        .setPlaybackId(playbackId)
        .setOperation(operation(generation))
        .setSegmentId("source-1:3")
        .setBoundaryIndex(3)
        .setDurationMs(durationMs)
        .setSource(SourceKind.SOURCE_KIND_TEXT_SOURCE)
        .setPlaybackProtocolRevision(DevicePlaybackTracker.PLAYBACK_PROTOCOL_REVISION)
        .setReceiverEpoch("receiver-1")
        .setCreditSequence(creditSequence)
        .setAttemptSequence(attemptSequence)
        .build()
}
