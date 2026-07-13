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
    val state = mutableState.asStateFlow()
    val messages = mutableMessages.asStateFlow()

    fun state(value: VoiceState) { mutableState.value = value }
    fun message(value: DisplayMessage) { mutableMessages.value += value }
}
