package works.ravd.radcliffe

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

sealed interface ContextUsageState {
    data object Loading : ContextUsageState
    data object Unavailable : ContextUsageState
    data class Available(val usage: ContextUsage) : ContextUsageState
}

data class RecentSessionItem(
    val summary: SessionSummary,
    val contextUsage: ContextUsageState,
)

enum class SessionHomeProblem { OFFLINE, UNAUTHORIZED }

data class SessionHomeUiState(
    val loading: Boolean = false,
    val loadingMore: Boolean = false,
    val items: List<RecentSessionItem> = emptyList(),
    val nextCursor: String? = null,
    val problem: SessionHomeProblem? = null,
    val problemDetail: String? = null,
)

internal fun sessionHomeLoading(
    current: SessionHomeUiState,
    more: Boolean = false,
): SessionHomeUiState = if (more) {
    current.copy(loadingMore = true, problem = null, problemDetail = null)
} else {
    current.copy(loading = true, problem = null, problemDetail = null)
}

internal fun sessionHomeLoaded(
    items: List<RecentSessionItem>,
    nextCursor: String?,
): SessionHomeUiState = SessionHomeUiState(items = items, nextCursor = nextCursor)

internal fun sessionHomePageAppended(
    current: SessionHomeUiState,
    items: List<RecentSessionItem>,
    nextCursor: String?,
): SessionHomeUiState = current.copy(
    items = (current.items + items).distinctBy { it.summary.sessionId },
    nextCursor = nextCursor,
    loading = false,
    loadingMore = false,
    problem = null,
    problemDetail = null,
)

internal fun sessionHomeFailure(
    current: SessionHomeUiState,
    error: Throwable,
): SessionHomeUiState = current.copy(
    loading = false,
    loadingMore = false,
    problem = if (error is ControlApiException && error.statusCode == 401) {
        SessionHomeProblem.UNAUTHORIZED
    } else {
        SessionHomeProblem.OFFLINE
    },
    problemDetail = error.message,
)

internal fun shouldRefreshSessionHome(previous: VoiceState, current: VoiceState): Boolean =
    current == VoiceState.IDLE && previous !in setOf(
        VoiceState.IDLE,
        VoiceState.CONNECTING,
        VoiceState.FAILED,
    )

enum class ContextSeverity { NORMAL, WARNING, NEAR_LIMIT, OVER_LIMIT, UNKNOWN, UNAVAILABLE }

data class ContextIndicatorModel(
    val severity: ContextSeverity,
    val fraction: Float,
    val centerLabel: String,
    val statusLabel: String,
    val accessibilityLabel: String,
    val progress: Boolean,
)

internal fun contextIndicator(state: ContextUsageState): ContextIndicatorModel = when (state) {
    ContextUsageState.Loading -> ContextIndicatorModel(
        ContextSeverity.UNKNOWN,
        0f,
        "…",
        "Usage loading",
        "Context usage loading",
        false,
    )
    ContextUsageState.Unavailable -> ContextIndicatorModel(
        ContextSeverity.UNAVAILABLE,
        0f,
        "—",
        "Usage unavailable",
        "Context usage unavailable",
        false,
    )
    is ContextUsageState.Available -> {
        val usage = state.usage
        val percentage = usage.percentage.coerceIn(0.0, 100.0)
        val severity = when {
            usage.overflow || percentage >= 100.0 -> ContextSeverity.OVER_LIMIT
            percentage >= 90.0 -> ContextSeverity.NEAR_LIMIT
            percentage >= 75.0 -> ContextSeverity.WARNING
            else -> ContextSeverity.NORMAL
        }
        val prefix = if (usage.exact) "" else "~"
        val center = when (severity) {
            ContextSeverity.WARNING -> "!${percentage.toInt()}%"
            ContextSeverity.NEAR_LIMIT -> "!!${percentage.toInt()}%"
            ContextSeverity.OVER_LIMIT -> "×100%"
            else -> "$prefix${percentage.toInt()}%"
        }
        val level = when (severity) {
            ContextSeverity.NORMAL -> "Context normal"
            ContextSeverity.WARNING -> "Context warning"
            ContextSeverity.NEAR_LIMIT -> "Context nearly full"
            ContextSeverity.OVER_LIMIT -> "Context over limit"
            else -> "Context unknown"
        }
        val estimate = if (usage.exact) "exact count" else "estimated count"
        val remainingPercent = (100.0 - percentage).coerceAtLeast(0.0).toInt()
        ContextIndicatorModel(
            severity = severity,
            fraction = (percentage / 100.0).toFloat(),
            centerLabel = center,
            statusLabel = "$level · $estimate",
            accessibilityLabel =
                "$level, ${percentage.toInt()} percent used, $remainingPercent percent remaining, $estimate",
            progress = true,
        )
    }
}

internal fun formatSessionDate(instant: Instant): String = DateTimeFormatter
    .ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
    .withZone(ZoneId.systemDefault())
    .format(instant)

@Composable
fun SessionHome(
    state: SessionHomeUiState,
    selectedSessionId: String,
    onNewSession: () -> Unit,
    onOpenSession: (String) -> Unit,
    onRetry: () -> Unit,
    onLoadMore: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Button(onClick = onNewSession, modifier = Modifier.fillMaxWidth()) {
            Text("New conversation")
        }
        when {
            state.loading && state.items.isEmpty() -> {
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 24.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(Modifier.size(28.dp))
                    Text("Loading recent conversations…", Modifier.padding(start = 12.dp))
                }
            }
            state.problem != null && state.items.isEmpty() -> SessionHomeProblemCard(
                state.problem,
                state.problemDetail,
                onRetry,
            )
            state.items.isEmpty() -> {
                Text("No conversations yet")
                Text("Start a new conversation and it will appear here.")
            }
            else -> {
                if (state.problem != null) {
                    SessionHomeProblemCard(state.problem, state.problemDetail, onRetry)
                }
                Text("Recent conversations", style = MaterialTheme.typography.titleMedium)
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(state.items, key = { it.summary.sessionId }) { item ->
                        SessionRow(
                            item,
                            selected = item.summary.sessionId == selectedSessionId,
                            onClick = { onOpenSession(item.summary.sessionId) },
                        )
                    }
                    if (state.nextCursor != null) {
                        item(key = "load-more") {
                            OutlinedButton(
                                onClick = onLoadMore,
                                enabled = !state.loadingMore,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(if (state.loadingMore) "Loading…" else "Load more")
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SessionHomeProblemCard(
    problem: SessionHomeProblem,
    detail: String?,
    onRetry: () -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                if (problem == SessionHomeProblem.UNAUTHORIZED) {
                    "Saved credential was rejected"
                } else {
                    "Recent conversations are unavailable"
                },
                style = MaterialTheme.typography.titleSmall,
            )
            Text(
                if (problem == SessionHomeProblem.UNAUTHORIZED) {
                    "Retry or update Connection settings."
                } else {
                    detail ?: "Check the control plane and network, then retry."
                },
            )
            OutlinedButton(onClick = onRetry) { Text("Retry") }
        }
    }
}

@Composable
private fun SessionRow(
    item: RecentSessionItem,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val summary = item.summary
    val date = formatSessionDate(summary.lastActivityAt)
    val title = summary.title?.takeIf { it.isNotBlank() } ?: "Conversation from $date"
    val indicator = contextIndicator(item.contextUsage)
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ContextDonut(indicator)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(date, style = MaterialTheme.typography.bodySmall)
                Text(indicator.statusLabel, style = MaterialTheme.typography.labelSmall)
                if (selected) {
                    Text("Most recently opened", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@Composable
private fun ContextDonut(model: ContextIndicatorModel) {
    val color = when (model.severity) {
        ContextSeverity.NORMAL -> MaterialTheme.colorScheme.primary
        ContextSeverity.WARNING -> MaterialTheme.colorScheme.tertiary
        ContextSeverity.NEAR_LIMIT, ContextSeverity.OVER_LIMIT -> MaterialTheme.colorScheme.error
        ContextSeverity.UNKNOWN -> MaterialTheme.colorScheme.secondary
        ContextSeverity.UNAVAILABLE -> MaterialTheme.colorScheme.outline
    }
    val track = MaterialTheme.colorScheme.surfaceVariant
    Box(
        Modifier
            .size(58.dp)
            .clearAndSetSemantics {
                contentDescription = model.accessibilityLabel
                if (model.progress) {
                    progressBarRangeInfo = ProgressBarRangeInfo(model.fraction, 0f..1f)
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size(52.dp)) {
            drawCircle(track, style = Stroke(width = 6.dp.toPx()))
            if (model.fraction > 0f) {
                drawArc(
                    color = color,
                    startAngle = -90f,
                    sweepAngle = 360f * model.fraction,
                    useCenter = false,
                    style = Stroke(width = 6.dp.toPx(), cap = StrokeCap.Round),
                )
            }
        }
        Text(
            model.centerLabel,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            maxLines = 1,
        )
    }
}
