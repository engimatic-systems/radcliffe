package works.ravd.radcliffe

import android.media.AudioAttributes
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import works.ravd.radcliffe.protocol.v2.JobEventKind

class EarconsTest {
    @Test
    fun cuesUseTheLiveKitCallRouteInsteadOfTheIndependentlyMutedSystemStream() {
        assertEquals(AudioAttributes.USAGE_VOICE_COMMUNICATION, EARCON_AUDIO_ROUTE.usage)
        assertEquals(AudioAttributes.CONTENT_TYPE_SPEECH, EARCON_AUDIO_ROUTE.contentType)
    }

    @Test
    fun allCuesAreLocalDistinctAndShortEnoughToAvoidMaskingSpeech() {
        assertEquals(Earcon.entries.toSet(), EarconCues.definitions.keys)
        assertTrue(EarconCues.definitions.values.all { cue ->
            cue.durationMillis < EarconCue.MAX_EARCON_DURATION_MILLIS &&
                cue.notes.all { it.startMillis + it.durationMillis <= cue.durationMillis }
        })

        val rendered = EarconCues.definitions.mapValues { (_, cue) -> EarconRenderer.render(cue) }
        rendered.forEach { (earcon, pcm) ->
            val cue = EarconCues.definitions.getValue(earcon)
            assertEquals(cue.durationMillis * pcm.sampleRateHz / 1_000, pcm.samples.size)
            assertTrue(pcm.samples.any { it != 0.toShort() })
        }
        assertEquals(
            Earcon.entries.size,
            rendered.values.map { it.samples.contentHashCode() }.toSet().size,
        )
    }

    @Test
    fun renderedCuesProduceStandardMonoPcmWaveFiles() {
        val pcm = EarconRenderer.render(EarconCues.definitions.getValue(Earcon.SESSION_CONNECTED))
        val wave = pcm.toWaveBytes()
        val header = ByteBuffer.wrap(wave).order(ByteOrder.LITTLE_ENDIAN)

        assertArrayEquals("RIFF".toByteArray(), wave.copyOfRange(0, 4))
        assertArrayEquals("WAVE".toByteArray(), wave.copyOfRange(8, 12))
        assertArrayEquals("fmt ".toByteArray(), wave.copyOfRange(12, 16))
        assertArrayEquals("data".toByteArray(), wave.copyOfRange(36, 40))
        assertEquals(wave.size - 8, header.getInt(4))
        assertEquals(1, header.getShort(20).toInt())
        assertEquals(1, header.getShort(22).toInt())
        assertEquals(pcm.sampleRateHz, header.getInt(24))
        assertEquals(16, header.getShort(34).toInt())
        assertEquals(pcm.samples.size * Short.SIZE_BYTES, header.getInt(40))
        assertEquals(44 + pcm.samples.size * Short.SIZE_BYTES, wave.size)
    }

    @Test
    fun cueTableKeepsTheRequestedInstrumentsAndVoicingsEasyToTune() {
        val connected = EarconCues.definitions.getValue(Earcon.SESSION_CONNECTED)
        val disconnected = EarconCues.definitions.getValue(Earcon.SESSION_DISCONNECTED)
        val thinking = EarconCues.definitions.getValue(Earcon.ASSISTANT_THINKING)
        val source = EarconCues.definitions.getValue(Earcon.READ_SOURCE_STARTED)

        assertEquals(EarconInstrument.VIBRAPHONE, connected.instrument)
        assertEquals(listOf(76), connected.notes.map(EarconNote::midiNote)) // E5
        assertEquals(listOf(48), disconnected.notes.map(EarconNote::midiNote)) // C3
        assertEquals(listOf(60, 72), thinking.notes.map(EarconNote::midiNote)) // C4 -> C5
        assertEquals(EarconInstrument.ELECTRIC_PIANO, source.instrument)
        assertEquals(listOf(50, 57, 61, 64), source.notes.map(EarconNote::midiNote))
    }

    @Test
    fun onlyMeaningfulVoiceTransitionsTriggerCues() {
        assertEquals(
            Earcon.SESSION_CONNECTED,
            earconForStateTransition(VoiceState.CONNECTING, VoiceState.LISTENING),
        )
        assertEquals(
            Earcon.SESSION_CONNECTED,
            earconForStateTransition(VoiceState.RECONNECTING, VoiceState.LISTENING),
        )
        assertEquals(
            Earcon.ASSISTANT_THINKING,
            earconForStateTransition(VoiceState.LISTENING, VoiceState.THINKING),
        )
        assertEquals(
            Earcon.READ_SOURCE_STARTED,
            earconForStateTransition(VoiceState.THINKING, VoiceState.READING),
        )
        assertEquals(null, earconForStateTransition(VoiceState.THINKING, VoiceState.THINKING))
        assertEquals(null, earconForStateTransition(VoiceState.SPEAKING, VoiceState.LISTENING))
    }

    @Test
    fun onlyAcceptedJobLifecycleChangesProduceStartAndSuccessCues() {
        assertEquals(
            Earcon.BACKGROUND_JOB_STARTED,
            earconForJobEvent(JobEventKind.JOB_EVENT_KIND_STARTED),
        )
        assertEquals(
            Earcon.BACKGROUND_JOB_COMPLETED,
            earconForJobEvent(JobEventKind.JOB_EVENT_KIND_SUCCEEDED),
        )
        assertEquals(null, earconForJobEvent(JobEventKind.JOB_EVENT_KIND_PROGRESS))
        assertEquals(null, earconForJobEvent(JobEventKind.JOB_EVENT_KIND_FAILED))
    }
}
