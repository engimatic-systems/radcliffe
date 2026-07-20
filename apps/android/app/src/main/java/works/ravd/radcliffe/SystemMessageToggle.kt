package works.ravd.radcliffe

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.toggleable
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp

data class SystemMessageToggleModel(
    val actionLabel: String,
    val stateLabel: String,
    val filled: Boolean,
)

internal fun systemMessageToggleModel(checked: Boolean): SystemMessageToggleModel =
    SystemMessageToggleModel(
        actionLabel = if (checked) "Hide system messages" else "Show system messages",
        stateLabel = if (checked) "Checked" else "Not checked",
        filled = checked,
    )

internal fun systemMessageIconColor(checked: Boolean, darkTheme: Boolean): Color = when {
    checked && darkTheme -> Color(0xFFFFB77A)
    checked -> Color(0xFFA54700)
    darkTheme -> Color(0xFFBDC1C6)
    else -> Color(0xFF5F6368)
}

internal fun systemMessageCheckColor(darkTheme: Boolean): Color =
    if (darkTheme) Color(0xFF321300) else Color.White

@Composable
fun SystemMessageToggle(
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val model = systemMessageToggleModel(checked)
    val darkTheme = isSystemInDarkTheme()
    val iconColor = systemMessageIconColor(checked, darkTheme)
    Box(
        contentAlignment = Alignment.Center,
        modifier = modifier
            .size(48.dp)
            .toggleable(
                value = checked,
                role = Role.Switch,
                onValueChange = onCheckedChange,
            )
            .semantics {
                contentDescription = model.actionLabel
                stateDescription = model.stateLabel
                role = Role.Switch
            },
    ) {
        Canvas(Modifier.size(20.dp)) {
            if (model.filled) {
                drawRect(iconColor)
                val checkColor = systemMessageCheckColor(darkTheme)
                drawLine(
                    checkColor,
                    start = center.copy(x = size.width * 0.22f, y = size.height * 0.52f),
                    end = center.copy(x = size.width * 0.43f, y = size.height * 0.73f),
                    strokeWidth = 2.5.dp.toPx(),
                )
                drawLine(
                    checkColor,
                    start = center.copy(x = size.width * 0.43f, y = size.height * 0.73f),
                    end = center.copy(x = size.width * 0.80f, y = size.height * 0.28f),
                    strokeWidth = 2.5.dp.toPx(),
                )
            } else {
                drawRect(iconColor, style = Stroke(width = 2.dp.toPx()))
            }
        }
    }
}
