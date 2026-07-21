package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionModelTest {
    @Test
    fun nonDefaultSpeechDeliveryHasAnExplicitAccessibilityDescription() {
        assertEquals(
            "Speech delivery: Fast (1.25×)",
            speechDeliveryDescription("Fast (1.25×)"),
        )
    }

    @Test
    fun prepareClearsThePreviousConversationBeforeLoadingOrConnecting() {
        SessionModel.open(
            "old-session",
            listOf(DisplayMessage("old", MessageKind.MODEL, "Old response")),
        )

        SessionModel.prepare("next-session")

        assertEquals("next-session", SessionModel.sessionId.value)
        assertTrue(SessionModel.messages.value.isEmpty())
    }

    @Test
    fun restoredAndLiveMessagesAreDeduplicatedByMessageId() {
        SessionModel.open(
            "session",
            listOf(DisplayMessage("model-1", MessageKind.MODEL, "Persisted")),
        )

        SessionModel.message(DisplayMessage("model-1", MessageKind.MODEL, "Live update"))

        assertEquals(1, SessionModel.messages.value.size)
        assertEquals("Live update", SessionModel.messages.value.single().text)
    }

    @Test
    fun listeningRequiresWorkerReadinessForTheActiveSession() {
        assertEquals(
            VoiceState.LISTENING,
            stateAfterSessionReady("session", "session", VoiceState.CONNECTING),
        )
        assertEquals(
            VoiceState.CONNECTING,
            stateAfterSessionReady("session", "stale-session", VoiceState.CONNECTING),
        )
        assertEquals(
            VoiceState.THINKING,
            stateAfterSessionReady("session", "session", VoiceState.THINKING),
        )
    }
}
