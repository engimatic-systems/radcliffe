package works.ravd.radcliffe

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@Composable
fun BackgroundJobsCard(
    jobs: List<BackgroundJobSummary>,
    commandInFlightJobId: String?,
    notice: String?,
    onFollowUp: (BackgroundJobSummary, String) -> Unit,
    onPriority: (BackgroundJobSummary, Int) -> Unit,
    onCancel: (BackgroundJobSummary) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (jobs.isEmpty() && notice == null) return
    var expanded by remember(jobs.firstOrNull()?.sessionId) { mutableStateOf(false) }
    val summary = backgroundJobsSummaryModel(jobs)
    Card(modifier.fillMaxWidth()) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Background jobs", Modifier.semantics { heading() })
                    Text(
                        summary.visibleLabel,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (jobs.isNotEmpty()) {
                    TextButton(
                        onClick = { expanded = !expanded },
                        modifier = Modifier.semantics {
                            contentDescription = if (expanded) {
                                "Hide ${summary.accessibilityLabel}"
                            } else {
                                "Show ${summary.accessibilityLabel}"
                            }
                        },
                    ) { Text(if (expanded) "Hide" else "Show") }
                }
            }
            if (jobs.isNotEmpty() && expanded) {
                LazyColumn(
                    Modifier.fillMaxWidth().heightIn(max = 280.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(jobs, key = { it.jobId }) { job ->
                        BackgroundJobRow(
                            job = job,
                            commandInFlight = commandInFlightJobId == job.jobId,
                            commandsBusy = commandInFlightJobId != null,
                            onFollowUp = onFollowUp,
                            onPriority = onPriority,
                            onCancel = onCancel,
                        )
                    }
                }
            }
            notice?.let {
                Text(it, Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            }
        }
    }
}

@Composable
private fun BackgroundJobRow(
    job: BackgroundJobSummary,
    commandInFlight: Boolean,
    commandsBusy: Boolean,
    onFollowUp: (BackgroundJobSummary, String) -> Unit,
    onPriority: (BackgroundJobSummary, Int) -> Unit,
    onCancel: (BackgroundJobSummary) -> Unit,
) {
    val model = backgroundJobCardModel(job)
    var followUp by remember(job.jobId) { mutableStateOf("") }
    val controlsEnabled = model.controlsEnabled && !commandsBusy
    Column(
        Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            Modifier.fillMaxWidth().semantics(mergeDescendants = true) {
                contentDescription = model.accessibilityLabel
            },
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                model.statusLabel,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text("${model.priorityLabel} priority")
        }
        if (job.entityIds.isNotEmpty() || job.artifactIds.isNotEmpty()) {
            Text(
                buildList {
                    if (job.entityIds.isNotEmpty()) add("${job.entityIds.size} results")
                    if (job.artifactIds.isNotEmpty()) add("${job.artifactIds.size} artifacts")
                }.joinToString(" · "),
            )
        }
        if (!job.status.terminal) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                listOf(0 to "Urgent", 2 to "Normal", 4 to "Background").forEach {
                        (priority, label) ->
                    TextButton(
                        onClick = { onPriority(job, priority) },
                        enabled = controlsEnabled && priority != job.priority,
                    ) { Text(label) }
                }
            }
            OutlinedTextField(
                value = followUp,
                onValueChange = { followUp = it.take(4_000) },
                label = { Text("Add a follow-up") },
                enabled = controlsEnabled,
                modifier = Modifier.fillMaxWidth(),
                minLines = 1,
                maxLines = 3,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = {
                        val instruction = followUp.trim()
                        if (instruction.isNotEmpty()) {
                            onFollowUp(job, instruction)
                            followUp = ""
                        }
                    },
                    enabled = controlsEnabled && followUp.isNotBlank(),
                ) { Text(if (commandInFlight) "Sending…" else "Send follow-up") }
                TextButton(
                    onClick = { onCancel(job) },
                    enabled = controlsEnabled,
                ) { Text("Cancel job") }
            }
        }
    }
}
