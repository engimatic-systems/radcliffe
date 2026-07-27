package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionModelTest {
    @Test
    fun pendingAgentControlIsClearedAcrossSessionHandoff() {
        val request = works.ravd.radcliffe.protocol.v2.AgentControlActionRequest.newBuilder()
            .setRequestId("session-switch-1")
            .build()
        AgentControlModel.request(
            PendingAgentControl(request, "Switch conversation?", "Switch to project?"),
        )

        SessionModel.open("destination", emptyList())

        assertEquals(null, AgentControlModel.pending.value)
    }

    @Test
    fun liveConfigurationUpdatesAreVisibleAndClearedAcrossSessions() {
        BehaviorConfigurationModel.update(
            ConfigurationUpdate(
                key = "response_detail",
                value = "brief",
                effectiveScope = "session",
                revision = 1,
                mutationId = "configuration-1",
            ),
        )

        assertEquals("brief", BehaviorConfigurationModel.updates.value["response_detail"]?.value)

        SessionModel.prepare("next-session")

        assertTrue(BehaviorConfigurationModel.updates.value.isEmpty())
    }

    @Test
    fun onlyTurnConfigurationUpdatesExpireWhenListeningResumes() {
        BehaviorConfigurationModel.clear()
        BehaviorConfigurationModel.update(
            ConfigurationUpdate("response_detail", "brief", "session", 1, "session-mutation"),
        )
        BehaviorConfigurationModel.update(
            ConfigurationUpdate("default_speech_rate", "1.5x", "turn", 1, "turn-mutation"),
        )

        BehaviorConfigurationModel.expireTurnOverrides()

        assertEquals("brief", BehaviorConfigurationModel.updates.value["response_detail"]?.value)
        assertFalse(BehaviorConfigurationModel.updates.value.containsKey("default_speech_rate"))
    }

    @Test
    fun staleConfigurationUpdatesCannotReplaceNewerEffectiveState() {
        BehaviorConfigurationModel.clear()
        BehaviorConfigurationModel.update(
            ConfigurationUpdate("response_detail", "detailed", "session", 4, "new"),
        )
        BehaviorConfigurationModel.update(
            ConfigurationUpdate("response_detail", "brief", "turn", 3, "stale"),
        )

        val effective = BehaviorConfigurationModel.updates.value.getValue("response_detail")
        assertEquals("detailed", effective.value)
        assertEquals(4, effective.revision)
    }

    @Test
    fun nonDefaultSpeechDeliveryHasAnExplicitAccessibilityDescription() {
        assertEquals(
            "Speech delivery: Fast (1.25×)",
            speechDeliveryDescription("Fast (1.25×)"),
        )
    }

    @Test
    fun machineDetailAffordanceNeverPlacesRawDetailInDefaultTalkBackLabels() {
        val raw = "Job job_0123456789abcdef wrote /srv/private/report.json"
        val message = DisplayMessage(
            id = "model-detail",
            kind = MessageKind.MODEL,
            text = raw,
            technicalDetailCategories = setOf("identifier", "path", "host-secret"),
        )

        val model = machineDetailUiModel(message)!!

        assertEquals("Technical detail available", model.visibleLabel)
        assertEquals("Read exact technical detail", model.actionLabel)
        assertTrue(model.accessibilityLabel.contains("not read automatically"))
        assertFalse(model.accessibilityLabel.contains("job_0123456789abcdef"))
        assertFalse(model.accessibilityLabel.contains("/srv/private/report.json"))
        assertEquals(
            setOf("identifier", "path"),
            technicalDetailCategories(message.kind, message.technicalDetailCategories),
        )
    }

    @Test
    fun ordinaryAndNonModelMessagesKeepTheirExistingTimelineSemantics() {
        assertEquals(
            null,
            machineDetailUiModel(DisplayMessage("ordinary", MessageKind.MODEL, "Ordinary answer")),
        )
        assertEquals(
            null,
            machineDetailUiModel(
                DisplayMessage(
                    id = "user",
                    kind = MessageKind.USER,
                    text = "job_0123456789abcdef",
                    technicalDetailCategories = setOf("identifier"),
                ),
            ),
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
