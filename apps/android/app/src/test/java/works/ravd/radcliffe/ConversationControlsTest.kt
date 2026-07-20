package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationControlsTest {
    private fun setting(
        key: String,
        value: String,
        revision: Int = 1,
    ): RegisteredConfiguration = RegisteredConfiguration(
        key = key,
        value = value,
        effectiveScope = "session",
        revision = revision,
        defaultValue = if (key == "response_detail") "standard" else "1.0x",
        allowedValues = if (key == "response_detail") {
            listOf("brief", "standard", "detailed")
        } else {
            listOf("0.75x", "1.0x", "1.25x", "1.5x")
        },
    )

    @Test
    fun registeredSpeechRateCycleShowsOnlyTheCurrentEffectiveValue() {
        val cycle = listOf("0.75x", "1.0x", "1.25x", "1.5x")

        cycle.forEachIndexed { index, current ->
            val control = configurationCycleControls(
                listOf(setting("default_speech_rate", current)),
                mutationInFlight = false,
            ).single()

            assertEquals(current, control.displayValue)
            assertEquals(cycle[(index + 1) % cycle.size], control.nextValue)
            assertTrue(control.accessibilityLabel.contains("Current value $current"))
            assertFalse(control.accessibilityLabel.contains("Current value ${control.nextValue}"))
        }
    }

    @Test
    fun responseDetailCycleUsesCompactSymbolsAndDescriptiveTalkBackLabels() {
        val cycle = listOf("brief", "standard", "detailed")
        val symbols = listOf("+", "++", "+++")

        cycle.forEachIndexed { index, current ->
            val control = configurationCycleControls(
                listOf(setting("response_detail", current)),
                mutationInFlight = false,
            ).single()

            assertEquals(symbols[index], control.displayValue)
            assertEquals(cycle[(index + 1) % cycle.size], control.nextValue)
            assertTrue(control.accessibilityLabel.contains("Current value $current"))
        }
    }

    @Test
    fun pendingAndInvalidRegistryStateNeverPresentARequestedValueAsApplied() {
        val current = setting("default_speech_rate", "1.0x")
        val pending = configurationCycleControls(listOf(current), mutationInFlight = true).single()
        val invalid = configurationCycleControls(
            listOf(current.copy(value = "2.0x")),
            mutationInFlight = false,
        ).single()

        assertEquals("1.0x", pending.displayValue)
        assertFalse(pending.enabled)
        assertTrue(pending.accessibilityLabel.contains("Update in progress"))
        assertEquals("2.0x", invalid.displayValue)
        assertEquals(null, invalid.nextValue)
        assertFalse(invalid.enabled)
    }

    @Test
    fun restoredApiStateWinsOverStaleLiveUpdatesAndNewerUpdatesReconcile() {
        val restored = setting("response_detail", "detailed", revision = 4)
        val stale = ConfigurationUpdate("response_detail", "brief", "turn", 3, "old")
        val newer = ConfigurationUpdate("response_detail", "standard", "session", 5, "new")

        assertEquals(
            "detailed",
            effectiveConfigurations(listOf(restored), mapOf(restored.key to stale)).single().value,
        )
        val reconciled = effectiveConfigurations(
            listOf(restored),
            mapOf(restored.key to newer),
        ).single()
        assertEquals("standard", reconciled.value)
        assertEquals(5, reconciled.revision)
        assertEquals("session", reconciled.effectiveScope)
    }

    @Test
    fun compactControlsShowOnlyValuesWhileTalkBackRetainsSettingNames() {
        val controls = configurationCycleControls(
            listOf(
                setting("response_detail", "standard"),
                setting("default_speech_rate", "1.0x"),
            ),
            mutationInFlight = false,
        )

        assertEquals(listOf("Pace", "Detail"), controls.map { it.label })
        assertEquals(listOf("1.0x", "++"), controls.map { it.displayValue })
        assertTrue(controls.zip(listOf("Pace", "Detail")).all { (control, label) ->
            control.accessibilityLabel.startsWith(label)
        })
        assertTrue(controls.all { "Pace" !in it.displayValue && "Detail" !in it.displayValue })
        assertTrue(controls.all { it.accessibilityLabel.contains("Double tap") })
    }
}
