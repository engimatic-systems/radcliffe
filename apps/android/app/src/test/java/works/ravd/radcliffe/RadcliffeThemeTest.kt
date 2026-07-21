package works.ravd.radcliffe

import androidx.compose.material3.ColorScheme
import androidx.compose.ui.graphics.Color
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RadcliffeThemeTest {
    @Test
    fun lightAndDarkSchemesUseDifferentThemeAwareSurfaces() {
        val light = radcliffeLightColorScheme()
        val dark = radcliffeDarkColorScheme()

        assertNotEquals(light.background, dark.background)
        assertNotEquals(light.surface, dark.surface)
        assertNotEquals(light.surfaceVariant, dark.surfaceVariant)
    }

    @Test
    fun lightThemeTextAndMessageSurfacesMeetContrastRequirements() {
        assertAccessibleTheme(radcliffeLightColorScheme())
    }

    @Test
    fun darkThemeTextAndMessageSurfacesMeetContrastRequirements() {
        assertAccessibleTheme(radcliffeDarkColorScheme())
    }

    private fun assertAccessibleTheme(scheme: ColorScheme) {
        listOf(
            "app background" to (scheme.onBackground to scheme.background),
            "surface" to (scheme.onSurface to scheme.surface),
            "settings and disabled status" to (scheme.onSurfaceVariant to scheme.surface),
            "user message" to (scheme.onSecondaryContainer to scheme.secondaryContainer),
            "model message" to (scheme.onPrimaryContainer to scheme.primaryContainer),
            "source message" to (scheme.onTertiaryContainer to scheme.tertiaryContainer),
            "system and error message" to (scheme.onErrorContainer to scheme.errorContainer),
            "normal context label" to (scheme.primary to scheme.surface),
            "warning context label" to (scheme.tertiary to scheme.surface),
            "near-limit context label" to (scheme.error to scheme.surface),
            "unavailable context label" to (scheme.onSurfaceVariant to scheme.surface),
        ).forEach { (role, colors) ->
            assertTrue(
                "$role contrast was ${contrastRatio(colors.first, colors.second)}",
                contrastRatio(colors.first, colors.second) >= 4.5,
            )
        }
        listOf(
            "normal context arc" to scheme.primary,
            "warning context arc" to scheme.tertiary,
            "near-limit context arc" to scheme.error,
        ).forEach { (role, color) ->
            assertTrue(
                "$role contrast was ${contrastRatio(color, scheme.surfaceVariant)}",
                contrastRatio(color, scheme.surfaceVariant) >= 3.0,
            )
        }
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
