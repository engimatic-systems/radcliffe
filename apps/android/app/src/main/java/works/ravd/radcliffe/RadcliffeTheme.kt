package works.ravd.radcliffe

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

internal fun radcliffeLightColorScheme(): ColorScheme = lightColorScheme()

internal fun radcliffeDarkColorScheme(): ColorScheme = darkColorScheme()

@Composable
fun RadcliffeTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) {
            radcliffeDarkColorScheme()
        } else {
            radcliffeLightColorScheme()
        },
        content = content,
    )
}
