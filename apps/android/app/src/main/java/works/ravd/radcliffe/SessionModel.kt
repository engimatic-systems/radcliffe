package works.ravd.radcliffe

import java.time.Instant
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class VoiceState { IDLE, CONNECTING, LISTENING, THINKING, SPEAKING, READING, PAUSED, RECONNECTING, FAILED }

enum class MessageKind { USER, MODEL, SOURCE, SYSTEM }

data class DisplayMessage(
    val id: String,
    val kind: MessageKind,
    val text: String,
    val provenance: String? = null,
    val deliveryLabel: String? = null,
    val createdAt: Instant? = null,
    val sequence: Long? = null,
)

data class ConfigurationUpdate(
    val key: String,
    val value: String,
    val effectiveScope: String,
    val revision: Int,
    val mutationId: String?,
)

data class PendingAgentControl(
    val request: works.ravd.radcliffe.protocol.v2.AgentControlActionRequest,
    val label: String,
    val detail: String,
)

object AgentControlModel {
    private val mutablePending = MutableStateFlow<PendingAgentControl?>(null)
    val pending = mutablePending.asStateFlow()

    fun request(value: PendingAgentControl) {
        mutablePending.value = value
    }

    fun clear(requestId: String? = null) {
        if (requestId == null || mutablePending.value?.request?.requestId == requestId) {
            mutablePending.value = null
        }
    }
}

object BehaviorConfigurationModel {
    private val mutableUpdates = MutableStateFlow<Map<String, ConfigurationUpdate>>(emptyMap())
    val updates = mutableUpdates.asStateFlow()

    fun update(value: ConfigurationUpdate) {
        mutableUpdates.value = mutableUpdates.value + (value.key to value)
    }

    fun expireTurnOverrides() {
        mutableUpdates.value = mutableUpdates.value.filterValues { it.effectiveScope != "turn" }
    }

    fun clear() {
        mutableUpdates.value = emptyMap()
    }
}

internal fun speechDeliveryDescription(label: String): String = "Speech delivery: $label"

internal fun stateAfterSessionReady(
    activeSessionId: String,
    readySessionId: String,
    current: VoiceState,
): VoiceState = if (
    activeSessionId == readySessionId && current in setOf(
        VoiceState.CONNECTING,
        VoiceState.RECONNECTING,
    )
) {
    VoiceState.LISTENING
} else {
    current
}

object SessionModel {
    private val mutableState = MutableStateFlow(VoiceState.IDLE)
    private val mutableMessages = MutableStateFlow<List<DisplayMessage>>(emptyList())
    private val mutableSessionId = MutableStateFlow("")
    val state = mutableState.asStateFlow()
    val messages = mutableMessages.asStateFlow()
    val sessionId = mutableSessionId.asStateFlow()

    fun state(value: VoiceState) { mutableState.value = value }
    fun prepare(sessionId: String = "") {
        BehaviorConfigurationModel.clear()
        AgentControlModel.clear()
        mutableSessionId.value = sessionId
        mutableMessages.value = emptyList()
    }
    fun open(sessionId: String, timeline: List<DisplayMessage>) {
        BehaviorConfigurationModel.clear()
        AgentControlModel.clear()
        mutableSessionId.value = sessionId
        mutableMessages.value = timeline.distinctBy { it.id }
    }
    fun message(value: DisplayMessage) {
        mutableMessages.value = mutableMessages.value
            .filterNot { it.id == value.id } + value
    }
}
