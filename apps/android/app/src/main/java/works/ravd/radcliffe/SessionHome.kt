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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import kotlin.math.ceil

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

internal fun sessionHomeSessionRemoved(
    current: SessionHomeUiState,
    sessionId: String,
): SessionHomeUiState = current.copy(
    items = current.items.filterNot { it.summary.sessionId == sessionId },
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

data class ResumeCostUiModel(
    val visibleLabel: String,
    val detail: String,
    val accessibilityLabel: String,
)

internal fun resumeCostUiModel(cost: ResumeCostEstimate): ResumeCostUiModel {
    val tokenBasis = if (cost.tokenBasis == "exact_projected_payload") {
        "exact measured model payload"
    } else {
        "estimated model payload"
    }
    val cache = if (cost.cacheAssumption == "cold") {
        "cold 5-minute cache-write or uncached-input assumption"
    } else {
        "warm cache-read assumption"
    }
    if (cost.noApiCharge) {
        val detail = "${cost.modelId} is local. Resuming has no provider API-token charge. " +
            "The estimate used ${cost.projectedInputTokens} $tokenBasis input tokens and " +
            "allows up to ${cost.representativeOutputTokens} output tokens."
        return ResumeCostUiModel("No API charge", detail, "No API charge. $detail")
    }
    if (!cost.available || cost.maximumUsd == null) {
        val reason = when (cost.unavailableReason) {
            "context_overflow" -> "the conversation exceeds the model context limit"
            "unknown_model" -> "the model has no reviewed pricing entry"
            "pricing_not_yet_effective" -> "the pricing entry is not effective yet"
            "pricing_expired" -> "the reviewed pricing entry expired"
            "pricing_tier_unavailable" -> "no reviewed long-context tier applies"
            "promotion_expired" -> "the promotional pricing entry expired"
            else -> "pricing or context accounting is unavailable"
        }
        val detail = "Resume cost unavailable for ${cost.modelId}: $reason."
        return ResumeCostUiModel("Resume cost unavailable", detail, detail)
    }
    val maximum = qualifiedUpperUsd(cost.maximumUsd)
    val qualifier = if (cost.tokenBasis == "exact_projected_payload") "Cold estimate" else "Approx. cold estimate"
    val visible = "$qualifier ≤$maximum"
    val priceDates = buildString {
        cost.pricingEffectiveDate?.let { append(" Pricing effective $it.") }
        cost.pricingExpiresOn?.let { append(" Pricing expires $it.") }
    }
    val rates = if (cost.inputRatePerMillion != null && cost.outputRatePerMillion != null) {
        " Input rate ${cost.inputRatePerMillion} USD per million; " +
            "output rate ${cost.outputRatePerMillion} USD per million."
    } else {
        ""
    }
    val detail = "$visible for ${cost.modelId} in ${cost.currency}. " +
        "Uses ${cost.projectedInputTokens} $tokenBasis input tokens, including measured " +
        "system and tool overhead, with up to ${cost.representativeOutputTokens} output tokens; " +
        "$cache.$priceDates$rates"
    return ResumeCostUiModel(visible, detail, "$visible. $detail")
}

internal fun unavailableResumeCostUiModel(): ResumeCostUiModel {
    val detail = "Resume cost unavailable because model-visible context accounting is unavailable."
    return ResumeCostUiModel("Resume cost unavailable", detail, detail)
}

private fun qualifiedUpperUsd(value: Double): String {
    if (value <= 0.0) return "$0.00"
    val scale = if (value < 0.01) 10_000.0 else 100.0
    val upper = ceil(value * scale) / scale
    return if (scale == 10_000.0) {
        String.format(Locale.US, "$%.4f", upper)
    } else {
        String.format(Locale.US, "$%.2f", upper)
    }
}

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
    archived: Boolean,
    mutationSessionId: String?,
    onNewSession: () -> Unit,
    onOpenSession: (String) -> Unit,
    onArchiveSession: (String) -> Unit,
    onRestoreSession: (String) -> Unit,
    onShowRecent: () -> Unit,
    onShowArchived: () -> Unit,
    onRetry: () -> Unit,
    onLoadMore: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var pendingArchive by remember { mutableStateOf<RecentSessionItem?>(null) }
    var costDetail by remember { mutableStateOf<ResumeCostUiModel?>(null) }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Button(onClick = onNewSession, modifier = Modifier.fillMaxWidth()) {
            Text("New conversation")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (archived) {
                OutlinedButton(onClick = onShowRecent) { Text("Recent") }
                Button(onClick = {}, enabled = false) { Text("Archived") }
            } else {
                Button(onClick = {}, enabled = false) { Text("Recent") }
                OutlinedButton(onClick = onShowArchived) { Text("Archived") }
            }
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
                Text(if (archived) "No archived conversations" else "No conversations yet")
                Text(
                    if (archived) {
                        "Archived conversations remain durable and can be restored here."
                    } else {
                        "Start a new conversation and it will appear here."
                    },
                )
            }
            else -> {
                if (state.problem != null) {
                    SessionHomeProblemCard(state.problem, state.problemDetail, onRetry)
                }
                Text(
                    if (archived) "Archived conversations" else "Recent conversations",
                    style = MaterialTheme.typography.titleMedium,
                )
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(state.items, key = { it.summary.sessionId }) { item ->
                        SessionRow(
                            item,
                            selected = item.summary.sessionId == selectedSessionId,
                            archived = archived,
                            mutationInFlight = mutationSessionId == item.summary.sessionId,
                            onClick = { onOpenSession(item.summary.sessionId) },
                            onArchive = { pendingArchive = item },
                            onRestore = { onRestoreSession(item.summary.sessionId) },
                            onCostDetail = { costDetail = it },
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
    pendingArchive?.let { item ->
        val summary = item.summary
        val date = formatSessionDate(summary.lastActivityAt)
        val title = summary.title?.takeIf { it.isNotBlank() } ?: "Conversation from $date"
        AlertDialog(
            onDismissRequest = { pendingArchive = null },
            title = { Text("Archive conversation?") },
            text = { Text("$title will leave Recent but keep its messages and audit history.") },
            confirmButton = {
                Button(
                    onClick = {
                        pendingArchive = null
                        onArchiveSession(summary.sessionId)
                    },
                ) { Text("Archive") }
            },
            dismissButton = {
                TextButton(onClick = { pendingArchive = null }) { Text("Cancel") }
            },
        )
    }
    costDetail?.let { model ->
        AlertDialog(
            onDismissRequest = { costDetail = null },
            title = { Text("Cold-cache resume estimate") },
            text = { Text(model.detail) },
            confirmButton = {
                Button(onClick = { costDetail = null }) { Text("Done") }
            },
        )
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
    archived: Boolean,
    mutationInFlight: Boolean,
    onClick: () -> Unit,
    onArchive: () -> Unit,
    onRestore: () -> Unit,
    onCostDetail: (ResumeCostUiModel) -> Unit,
) {
    val summary = item.summary
    val date = formatSessionDate(summary.lastActivityAt)
    val title = summary.title?.takeIf { it.isNotBlank() } ?: "Conversation from $date"
    val indicator = contextIndicator(item.contextUsage)
    val resumeCost = when (val usage = item.contextUsage) {
        is ContextUsageState.Available -> resumeCostUiModel(usage.usage.resumeCost)
        ContextUsageState.Unavailable -> unavailableResumeCostUiModel()
        ContextUsageState.Loading -> null
    }
    Card(modifier = Modifier.fillMaxWidth()) {
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
                resumeCost?.let { cost ->
                    TextButton(
                        onClick = { onCostDetail(cost) },
                        modifier = Modifier.semantics {
                            contentDescription = cost.accessibilityLabel
                        },
                    ) { Text(cost.visibleLabel) }
                }
                if (selected) {
                    Text("Most recently opened", style = MaterialTheme.typography.labelSmall)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (archived) {
                        OutlinedButton(onClick = onRestore, enabled = !mutationInFlight) {
                            Text(if (mutationInFlight) "Restoring…" else "Restore")
                        }
                    } else {
                        OutlinedButton(onClick = onClick, enabled = !mutationInFlight) {
                            Text("Open")
                        }
                        TextButton(onClick = onArchive, enabled = !mutationInFlight) {
                            Text(if (mutationInFlight) "Archiving…" else "Archive")
                        }
                    }
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
        ContextSeverity.UNAVAILABLE -> MaterialTheme.colorScheme.onSurfaceVariant
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
