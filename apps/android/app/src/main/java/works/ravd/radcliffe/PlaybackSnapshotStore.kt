package works.ravd.radcliffe

import android.content.Context
import android.util.Base64
import works.ravd.radcliffe.protocol.v2.PlaybackAcknowledgement

/**
 * App-private, content-free receiver evidence used only to make process restart
 * reconciliation idempotent. No PCM or conversation text is stored.
 */
class PlaybackSnapshotStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun load(): List<PlaybackAcknowledgement> =
        preferences.getString(SNAPSHOTS, "")
            .orEmpty()
            .lineSequence()
            .filter(String::isNotBlank)
            .mapNotNull { encoded ->
                runCatching {
                    PlaybackAcknowledgement.parseFrom(
                        Base64.decode(encoded, Base64.NO_WRAP),
                    )
                }.getOrNull()
            }
            .toList()

    fun save(acknowledgements: List<PlaybackAcknowledgement>) {
        val encoded = acknowledgements.joinToString("\n") {
            Base64.encodeToString(it.toByteArray(), Base64.NO_WRAP)
        }
        check(preferences.edit().putString(SNAPSHOTS, encoded).commit()) {
            "failed to persist bounded-playback receiver evidence"
        }
    }

    private companion object {
        const val PREFERENCES = "bounded-playback"
        const val SNAPSHOTS = "receiver-evidence-v1"
    }
}
