package works.ravd.radcliffe

import android.content.Context
import android.media.AudioAttributes
import android.media.SoundPool
import android.util.Log
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import works.ravd.radcliffe.protocol.v2.JobEventKind

private const val WAVE_HEADER_BYTES = 44

/**
 * The small set of non-speech sounds used by the foreground voice session.
 *
 * The cue definitions below are intentionally the single place to tune an
 * instrument, note (MIDI pitch), onset, duration, or overall gain.  They are
 * rendered as PCM on the device: no audio is downloaded, transmitted, or put
 * into the conversation timeline.
 */
internal enum class Earcon {
    SESSION_CONNECTED,
    SESSION_DISCONNECTED,
    ASSISTANT_THINKING,
    BACKGROUND_JOB_STARTED,
    BACKGROUND_JOB_COMPLETED,
    READ_SOURCE_STARTED,
}

internal enum class EarconInstrument {
    VIBRAPHONE,
    ELECTRIC_PIANO,
}

internal data class EarconNote(
    /** MIDI note number: C4 is 60 and C5 is 72. */
    val midiNote: Int,
    val startMillis: Int,
    val durationMillis: Int,
)

internal data class EarconCue(
    val instrument: EarconInstrument,
    val notes: List<EarconNote>,
    val durationMillis: Int,
    val gain: Double,
) {
    init {
        require(notes.isNotEmpty()) { "An earcon needs at least one note" }
        require(durationMillis in 1 until MAX_EARCON_DURATION_MILLIS) {
            "Earcons must be shorter than ${MAX_EARCON_DURATION_MILLIS} ms"
        }
        require(gain in 0.0..1.0) { "Earcon gain must be normalized" }
        notes.forEach { note ->
            require(note.midiNote in 24..108) { "Earcon pitch must be audible" }
            require(note.startMillis >= 0 && note.durationMillis > 0) {
                "Earcon note timing must be positive"
            }
            require(note.startMillis + note.durationMillis <= durationMillis) {
                "Every note must fit inside its cue"
            }
        }
    }

    companion object {
        const val MAX_EARCON_DURATION_MILLIS = 500
    }
}

/**
 * Radcliffe's entire sound palette.  Change this table to audition another
 * voicing; the renderer and event wiring do not need to change.
 */
internal object EarconCues {
    val definitions: Map<Earcon, EarconCue> = mapOf(
        // A clear fifth-octave vibraphone note says that listening is ready.
        Earcon.SESSION_CONNECTED to EarconCue(
            instrument = EarconInstrument.VIBRAPHONE,
            notes = listOf(EarconNote(midiNote = 76, startMillis = 0, durationMillis = 230)), // E5
            durationMillis = 260,
            gain = 0.18,
        ),
        // A low third-octave counterpart makes a disconnect unambiguous.
        Earcon.SESSION_DISCONNECTED to EarconCue(
            instrument = EarconInstrument.VIBRAPHONE,
            notes = listOf(EarconNote(midiNote = 48, startMillis = 0, durationMillis = 260)), // C3
            durationMillis = 290,
            gain = 0.18,
        ),
        // C4 to C5: endpointing is complete and the assistant is working.
        Earcon.ASSISTANT_THINKING to EarconCue(
            instrument = EarconInstrument.VIBRAPHONE,
            notes = listOf(
                EarconNote(midiNote = 60, startMillis = 0, durationMillis = 140), // C4
                EarconNote(midiNote = 72, startMillis = 105, durationMillis = 190), // C5
            ),
            durationMillis = 325,
            gain = 0.17,
        ),
        // A rising C-major-six gesture feels expectant without sounding urgent.
        Earcon.BACKGROUND_JOB_STARTED to EarconCue(
            instrument = EarconInstrument.VIBRAPHONE,
            notes = listOf(
                EarconNote(midiNote = 60, startMillis = 0, durationMillis = 120), // C4
                EarconNote(midiNote = 64, startMillis = 85, durationMillis = 120), // E4
                EarconNote(midiNote = 69, startMillis = 170, durationMillis = 180), // A4
            ),
            durationMillis = 385,
            gain = 0.18,
        ),
        // C6/9 is compact and satisfying when a job succeeds.
        Earcon.BACKGROUND_JOB_COMPLETED to EarconCue(
            instrument = EarconInstrument.ELECTRIC_PIANO,
            notes = listOf(
                EarconNote(midiNote = 60, startMillis = 0, durationMillis = 250), // C4
                EarconNote(midiNote = 64, startMillis = 0, durationMillis = 250), // E4
                EarconNote(midiNote = 67, startMillis = 0, durationMillis = 250), // G4
                EarconNote(midiNote = 74, startMillis = 0, durationMillis = 250), // D5
            ),
            durationMillis = 290,
            gain = 0.20,
        ),
        // Dmaj7(add9): low, reassuring, with just enough jazz-piano color.
        Earcon.READ_SOURCE_STARTED to EarconCue(
            instrument = EarconInstrument.ELECTRIC_PIANO,
            notes = listOf(
                EarconNote(midiNote = 50, startMillis = 0, durationMillis = 290), // D3
                EarconNote(midiNote = 57, startMillis = 0, durationMillis = 290), // A3
                EarconNote(midiNote = 61, startMillis = 0, durationMillis = 290), // C#4
                EarconNote(midiNote = 64, startMillis = 0, durationMillis = 290), // E4
            ),
            durationMillis = 340,
            gain = 0.19,
        ),
    )
}

internal fun earconForStateTransition(previous: VoiceState, current: VoiceState): Earcon? = when {
    current == VoiceState.LISTENING && previous in setOf(
        VoiceState.CONNECTING,
        VoiceState.RECONNECTING,
    ) -> Earcon.SESSION_CONNECTED
    current == VoiceState.THINKING && previous != VoiceState.THINKING -> Earcon.ASSISTANT_THINKING
    current == VoiceState.READING && previous != VoiceState.READING -> Earcon.READ_SOURCE_STARTED
    else -> null
}

internal fun earconForJobEvent(kind: JobEventKind): Earcon? = when (kind) {
    JobEventKind.JOB_EVENT_KIND_STARTED -> Earcon.BACKGROUND_JOB_STARTED
    JobEventKind.JOB_EVENT_KIND_SUCCEEDED -> Earcon.BACKGROUND_JOB_COMPLETED
    else -> null
}

internal data class RenderedEarcon(
    val sampleRateHz: Int,
    val samples: ShortArray,
)

internal data class EarconAudioRoute(
    val usage: Int,
    val contentType: Int,
)

/**
 * Match LiveKit's audible call route rather than Pixel's independently mutable
 * system-sonification stream. SoundPool does not take focus from LiveKit.
 */
internal val EARCON_AUDIO_ROUTE = EarconAudioRoute(
    usage = AudioAttributes.USAGE_VOICE_COMMUNICATION,
    contentType = AudioAttributes.CONTENT_TYPE_SPEECH,
)

/** A deterministic, in-repository PCM renderer for the cue table above. */
internal object EarconRenderer {
    // 48 kHz is the native output rate used by Pixel/WebRTC devices. SoundPool
    // can still resample it when another route requires a different rate.
    const val SAMPLE_RATE_HZ = 48_000
    private const val MAX_PCM_AMPLITUDE = 32_767.0

    fun render(cue: EarconCue): RenderedEarcon {
        val sampleCount = cue.durationMillis * SAMPLE_RATE_HZ / 1_000
        val rendered = ShortArray(sampleCount)
        val normalizedVoiceGain = cue.gain / cue.notes.size
        for (sampleIndex in rendered.indices) {
            val value = cue.notes.sumOf { note ->
                val localMillis = sampleIndex * 1_000.0 / SAMPLE_RATE_HZ - note.startMillis
                if (localMillis !in 0.0..<note.durationMillis.toDouble()) {
                    0.0
                } else {
                    val localSeconds = localMillis / 1_000.0
                    val frequencyHz = midiToFrequency(note.midiNote)
                    normalizedVoiceGain * instrumentSample(
                        cue.instrument,
                        frequencyHz,
                        localSeconds,
                        note.durationMillis / 1_000.0,
                    )
                }
            }
            rendered[sampleIndex] = (value.coerceIn(-1.0, 1.0) * MAX_PCM_AMPLITUDE).toInt().toShort()
        }
        return RenderedEarcon(SAMPLE_RATE_HZ, rendered)
    }

    private fun midiToFrequency(midiNote: Int): Double = 440.0 * 2.0.pow((midiNote - 69) / 12.0)

    private fun instrumentSample(
        instrument: EarconInstrument,
        frequencyHz: Double,
        seconds: Double,
        durationSeconds: Double,
    ): Double = when (instrument) {
        EarconInstrument.VIBRAPHONE -> vibraphone(frequencyHz, seconds, durationSeconds)
        EarconInstrument.ELECTRIC_PIANO -> electricPiano(frequencyHz, seconds, durationSeconds)
    }

    private fun vibraphone(frequencyHz: Double, seconds: Double, durationSeconds: Double): Double {
        val vibrato = 0.004 * sin(2.0 * PI * 5.5 * seconds)
        val phase = 2.0 * PI * frequencyHz * seconds * (1.0 + vibrato)
        val partials =
            sin(phase) +
                0.33 * sin(2.01 * phase) +
                0.17 * sin(3.98 * phase) +
                0.07 * sin(5.37 * phase)
        val envelope = attackReleaseEnvelope(seconds, durationSeconds, attackSeconds = 0.008) *
            exp(-seconds / 0.42)
        return 0.72 * partials * envelope
    }

    private fun electricPiano(frequencyHz: Double, seconds: Double, durationSeconds: Double): Double {
        val carrier = 2.0 * PI * frequencyHz * seconds
        val fmIndex = 1.25 * exp(-seconds / 0.075)
        val bell = sin(carrier + fmIndex * sin(3.5 * carrier))
        val body = 0.50 * sin(carrier) + 0.12 * sin(2.0 * carrier)
        val envelope = attackReleaseEnvelope(seconds, durationSeconds, attackSeconds = 0.010) *
            exp(-seconds / 0.31)
        return 0.66 * (bell + body) * envelope
    }

    private fun attackReleaseEnvelope(
        seconds: Double,
        durationSeconds: Double,
        attackSeconds: Double,
    ): Double {
        val attack = min(1.0, seconds / attackSeconds)
        val releaseSeconds = 0.040
        val release = min(1.0, max(0.0, (durationSeconds - seconds) / releaseSeconds))
        return attack * release
    }
}

/** Wrap locally rendered mono PCM in a standard 16-bit WAV container. */
internal fun RenderedEarcon.toWaveBytes(): ByteArray {
    val dataSize = samples.size * Short.SIZE_BYTES
    return ByteBuffer.allocate(WAVE_HEADER_BYTES + dataSize)
        .order(ByteOrder.LITTLE_ENDIAN)
        .apply {
            put("RIFF".toByteArray(StandardCharsets.US_ASCII))
            putInt(36 + dataSize)
            put("WAVE".toByteArray(StandardCharsets.US_ASCII))
            put("fmt ".toByteArray(StandardCharsets.US_ASCII))
            putInt(16)
            putShort(1) // PCM
            putShort(1) // mono
            putInt(sampleRateHz)
            putInt(sampleRateHz * Short.SIZE_BYTES)
            putShort(Short.SIZE_BYTES.toShort())
            putShort(Short.SIZE_BITS.toShort())
            put("data".toByteArray(StandardCharsets.US_ASCII))
            putInt(dataSize)
            samples.forEach(::putShort)
        }
        .array()
}

internal data class EarconPlaybackDiagnostic(
    val sequence: Long = 0,
    val cue: Earcon? = null,
    val status: String = "No cue requested",
)

/**
 * Local, ephemeral playback state for the diagnostics panel. It is not a
 * conversation message and is never sent to the server or model.
 */
internal object EarconPlaybackDiagnostics {
    private val mutableState = kotlinx.coroutines.flow.MutableStateFlow(EarconPlaybackDiagnostic())
    val state = mutableState.asStateFlow()

    fun report(cue: Earcon?, status: String) {
        mutableState.value = EarconPlaybackDiagnostic(
            sequence = mutableState.value.sequence + 1,
            cue = cue,
            status = status,
        )
    }
}

/**
 * Plays one short sonification cue without taking audio focus from the remote
 * speech track.  Replacing a still-playing cue prevents a burst of lifecycle
 * events from turning into a noisy stack of sounds.
 */
internal class EarconPlayer(context: Context) {
    private val soundPool = SoundPool.Builder()
        .setMaxStreams(1)
        .setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(EARCON_AUDIO_ROUTE.usage)
                .setContentType(EARCON_AUDIO_ROUTE.contentType)
                .build(),
        )
        .build()
    private val sampleByCue = mutableMapOf<Earcon, Int>()
    private val cueBySample = mutableMapOf<Int, Earcon>()
    private val loadedSamples = mutableSetOf<Int>()
    private var pendingCue: Earcon? = null
    private var activeStream = 0
    private var released = false

    init {
        soundPool.setOnLoadCompleteListener { _, sampleId, status ->
            val cue = cueBySample[sampleId] ?: return@setOnLoadCompleteListener
            if (status != 0) {
                Log.w(TAG, "earcon sample load failed cue=$cue status=$status")
                EarconPlaybackDiagnostics.report(cue, "Sample load failed ($status)")
                return@setOnLoadCompleteListener
            }
            loadedSamples += sampleId
            Log.i(TAG, "earcon sample loaded cue=$cue sample=$sampleId")
            if (pendingCue == cue) {
                pendingCue = null
                play(cue)
            }
        }
        EarconCues.definitions.forEach { (cue, definition) ->
            val sampleId = runCatching {
                val rendered = EarconRenderer.render(definition)
                val file = File(context.cacheDir, "radcliffe-earcon-${cue.name.lowercase()}.wav")
                file.writeBytes(rendered.toWaveBytes())
                soundPool.load(file.absolutePath, 1)
            }.getOrElse { error ->
                Log.w(TAG, "earcon sample creation failed cue=$cue", error)
                EarconPlaybackDiagnostics.report(cue, "Sample creation failed")
                0
            }
            if (sampleId == 0) {
                Log.w(TAG, "earcon sample was not queued cue=$cue")
                EarconPlaybackDiagnostics.report(cue, "Sample was not queued")
            } else {
                sampleByCue[cue] = sampleId
                cueBySample[sampleId] = cue
            }
        }
    }

    fun play(earcon: Earcon) {
        if (released) {
            EarconPlaybackDiagnostics.report(earcon, "Player already released")
            return
        }
        val sampleId = sampleByCue[earcon]
        if (sampleId == null) {
            Log.w(TAG, "earcon sample unavailable cue=$earcon")
            EarconPlaybackDiagnostics.report(earcon, "Sample unavailable")
            return
        }
        if (sampleId !in loadedSamples) {
            pendingCue = earcon
            EarconPlaybackDiagnostics.report(earcon, "Waiting for sample load")
            return
        }
        if (activeStream != 0) soundPool.stop(activeStream)
        activeStream = soundPool.play(sampleId, 1.0f, 1.0f, 1, 0, 1.0f)
        if (activeStream == 0) {
            Log.w(TAG, "earcon playback did not start cue=$earcon")
            EarconPlaybackDiagnostics.report(earcon, "SoundPool rejected playback")
            return
        }
        Log.i(TAG, "earcon playback started cue=$earcon stream=$activeStream")
        EarconPlaybackDiagnostics.report(earcon, "Playback started")
    }

    fun release() {
        released = true
        soundPool.release()
        sampleByCue.clear()
        cueBySample.clear()
        loadedSamples.clear()
        activeStream = 0
    }

    private companion object {
        const val TAG = "RadcliffeEarcons"
    }
}
