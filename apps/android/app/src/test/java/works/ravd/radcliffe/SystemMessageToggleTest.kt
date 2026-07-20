package works.ravd.radcliffe

import androidx.compose.ui.graphics.Color
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SystemMessageToggleTest {
    @Test
    fun uncheckedAndCheckedStatesHaveActionAndNonColorCues() {
        val unchecked = systemMessageToggleModel(false)
        val checked = systemMessageToggleModel(true)

        assertEquals("Show system messages", unchecked.actionLabel)
        assertEquals("Not checked", unchecked.stateLabel)
        assertFalse(unchecked.filled)
        assertEquals("Hide system messages", checked.actionLabel)
        assertEquals("Checked", checked.stateLabel)
        assertTrue(checked.filled)
    }

    @Test
    fun offIsGrayscaleAndOnIsOrangeInLightAndDarkThemes() {
        listOf(false, true).forEach { dark ->
            val off = systemMessageIconColor(false, dark)
            val on = systemMessageIconColor(true, dark)

            assertEquals(off.red, off.green, 0.02f)
            assertEquals(off.green, off.blue, 0.02f)
            assertTrue(on.red > on.green)
            assertTrue(on.green > on.blue)
            assertNotEquals(off, on)
        }
    }

    @Test
    fun iconAndCheckMeetNonTextContrastInBothThemes() {
        listOf(false, true).forEach { dark ->
            val scheme = if (dark) radcliffeDarkColorScheme() else radcliffeLightColorScheme()
            val off = systemMessageIconColor(false, dark)
            val on = systemMessageIconColor(true, dark)
            val check = systemMessageCheckColor(dark)

            assertTrue(contrastRatio(off, scheme.surface) >= 3.0)
            assertTrue(contrastRatio(on, scheme.surface) >= 3.0)
            assertTrue(contrastRatio(check, on) >= 3.0)
        }
    }

    @Test
    fun rapidToggleSequenceAlwaysReflectsLatestPersistedBoolean() {
        val states = listOf(false, true, false, true, true, false)

        assertEquals(
            listOf("Not checked", "Checked", "Not checked", "Checked", "Checked", "Not checked"),
            states.map { systemMessageToggleModel(it).stateLabel },
        )
    }

    private fun contrastRatio(first: Color, second: Color): Double {
        val firstLuminance = luminance(first)
        val secondLuminance = luminance(second)
        return (max(firstLuminance, secondLuminance) + 0.05) /
            (min(firstLuminance, secondLuminance) + 0.05)
    }

    private fun luminance(color: Color): Double = 0.2126 * linear(color.red) +
        0.7152 * linear(color.green) +
        0.0722 * linear(color.blue)

    private fun linear(component: Float): Double {
        val value = component.toDouble()
        return if (value <= 0.04045) value / 12.92 else ((value + 0.055) / 1.055).pow(2.4)
    }
}
