package works.ravd.radcliffe

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class VoiceState { IDLE, CONNECTING, LISTENING, THINKING, SPEAKING, READING, PAUSED, RECONNECTING, FAILED }

enum class MessageKind { USER, MODEL, SOURCE, SYSTEM }

data class DisplayMessage(
    val id: String,
    val kind: MessageKind,
    val text: String,
    val provenance: String? = null,
)

object SessionModel {
    private val mutableState = MutableStateFlow(VoiceState.IDLE)
    private val mutableMessages = MutableStateFlow<List<DisplayMessage>>(emptyList())
    private val mutableSessionId = MutableStateFlow("")
    val state = mutableState.asStateFlow()
    val messages = mutableMessages.asStateFlow()
    val sessionId = mutableSessionId.asStateFlow()

    fun state(value: VoiceState) { mutableState.value = value }
    fun prepare(sessionId: String = "") {
        mutableSessionId.value = sessionId
        mutableMessages.value = emptyList()
    }
    fun open(sessionId: String, timeline: List<DisplayMessage>) {
        mutableSessionId.value = sessionId
        mutableMessages.value = timeline.distinctBy { it.id }
    }
    fun message(value: DisplayMessage) {
        mutableMessages.value = mutableMessages.value
            .filterNot { it.id == value.id } + value
    }
}
