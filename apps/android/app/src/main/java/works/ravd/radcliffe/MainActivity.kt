package works.ravd.radcliffe

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

private sealed interface SessionAction {
    data object Create : SessionAction
    data class Open(val sessionId: String) : SessionAction
}

private data class LoadedSessionPage(
    val items: List<RecentSessionItem>,
    val nextCursor: String?,
)

private data class PendingConversationExport(
    val profile: ConnectionProfile,
    val session: SessionSummary,
    val includeSystemMessages: Boolean,
)

private suspend inline fun <T> suspendRunCatching(
    crossinline block: suspend () -> T,
): Result<T> = try {
    Result.success(block())
} catch (error: CancellationException) {
    throw error
} catch (error: Throwable) {
    Result.failure(error)
}

class MainActivity : ComponentActivity() {
    private val profiles by lazy { ConnectionProfileRepository.create(this) }
    private val enrollmentMutex = Mutex()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { RadcliffeScreen() } } }
    }

    @Composable
    private fun RadcliffeScreen() {
        val state by SessionModel.state.collectAsState()
        val messages by SessionModel.messages.collectAsState()
        val activeSessionId by SessionModel.sessionId.collectAsState()
        val storedProfile by profiles.profile.collectAsState(initial = null)
        val showSystemMessages by profiles.showSystemMessages.collectAsState(initial = false)
        val scope = rememberCoroutineScope()
        var editing by remember { mutableStateOf(false) }
        var initialized by remember { mutableStateOf(false) }
        var controlUrl by remember { mutableStateOf("") }
        var bootstrapToken by remember { mutableStateOf("") }
        var settingsError by remember { mutableStateOf<String?>(null) }
        var settingsNotice by remember { mutableStateOf<String?>(null) }
        var homeState by remember { mutableStateOf(SessionHomeUiState()) }
        var refreshKey by remember { mutableIntStateOf(0) }
        var permissionAction by remember { mutableStateOf<SessionAction?>(null) }
        var grantedAction by remember { mutableStateOf<SessionAction?>(null) }
        var previousVoiceState by remember { mutableStateOf(state) }
        var diagnosticsVisible by remember { mutableStateOf(false) }
        var archivedView by remember { mutableStateOf(false) }
        var mutationSessionId by remember { mutableStateOf<String?>(null) }
        var operationNotice by remember { mutableStateOf<String?>(null) }
        var exportInFlight by remember { mutableStateOf(false) }
        var pendingExport by remember { mutableStateOf<PendingConversationExport?>(null) }
        var exportNotice by remember { mutableStateOf<String?>(null) }

        LaunchedEffect(storedProfile) {
            val profile = storedProfile ?: return@LaunchedEffect
            if (!initialized || !editing) {
                controlUrl = profile.controlUrl
                bootstrapToken = profile.bootstrapToken
                editing = !profile.isConfigured
                initialized = true
            }
        }

        LaunchedEffect(
            storedProfile?.controlUrl,
            storedProfile?.bootstrapToken,
            storedProfile?.deviceCredential,
            archivedView,
            refreshKey,
        ) {
            val profile = storedProfile ?: return@LaunchedEffect
            if (!profile.isConfigured || editing || state != VoiceState.IDLE) return@LaunchedEffect
            homeState = sessionHomeLoading(homeState)
            suspendRunCatching {
                withContext(Dispatchers.IO) {
                    loadSessionPage(profile, archived = archivedView)
                }
            }
                .onSuccess { loaded ->
                    homeState = sessionHomeLoaded(loaded.items, loaded.nextCursor)
                }
                .onFailure { error ->
                    homeState = sessionHomeFailure(homeState, error)
                    if (error is ControlApiException && error.statusCode == 401 && profile.isEnrolled) {
                        withContext(Dispatchers.IO) { profiles.invalidateEnrollment() }
                        settingsError = "Device credential was rejected. Enter the bootstrap token to re-enroll."
                    }
                }
        }

        LaunchedEffect(state) {
            if (shouldRefreshSessionHome(previousVoiceState, state)) {
                refreshKey += 1
            }
            previousVoiceState = state
        }

        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
            if (it) grantedAction = permissionAction
            permissionAction = null
        }

        val exportDocument = rememberLauncherForActivityResult(
            ActivityResultContracts.CreateDocument("text/markdown"),
        ) { uri ->
            val request = pendingExport
            pendingExport = null
            if (uri == null) {
                exportInFlight = false
                exportNotice = "Export canceled"
            } else if (request == null) {
                exportInFlight = false
                exportNotice = "Export request expired; try again"
            } else {
                exportNotice = "Writing export…"
                scope.launch {
                    suspendRunCatching {
                        withContext(Dispatchers.IO) {
                            val timeline = SessionApi.timeline(
                                request.profile.controlUrl,
                                request.profile.deviceCredential,
                                request.session.sessionId,
                            )
                            val markdown = renderConversationMarkdown(
                                ConversationExport(
                                    request.session,
                                    timeline,
                                    request.includeSystemMessages,
                                ),
                            )
                            val output = contentResolver.openOutputStream(uri, "wt")
                                ?: error("Android could not open the selected document")
                            output.bufferedWriter(Charsets.UTF_8).use { it.write(markdown) }
                        }
                    }.onSuccess {
                        exportNotice = "Conversation exported"
                    }.onFailure { error ->
                        exportNotice = "Export failed: ${error.message ?: "unknown error"}"
                    }
                    exportInFlight = false
                }
            }
        }

        fun beginExport(sessionId: String) {
            if (exportInFlight) return
            val profile = storedProfile
            if (profile == null || !profile.isConfigured) {
                exportNotice = "Connection settings are unavailable"
                return
            }
            exportInFlight = true
            exportNotice = "Preparing export…"
            scope.launch {
                suspendRunCatching {
                    withContext(Dispatchers.IO) {
                        val enrolled = ensureEnrolled(profile)
                        enrolled to SessionApi.get(
                            enrolled.controlUrl,
                            enrolled.deviceCredential,
                            sessionId,
                        )
                    }
                }.onSuccess { (enrolled, session) ->
                    pendingExport = PendingConversationExport(
                        enrolled,
                        session,
                        showSystemMessages,
                    )
                    exportDocument.launch(exportFileName(session))
                }.onFailure { error ->
                    exportInFlight = false
                    exportNotice = "Export failed: ${error.message ?: "unknown error"}"
                }
            }
        }

        fun updateSessionStatus(sessionId: String, status: String, current: Boolean = false) {
            if (mutationSessionId != null) return
            val profile = storedProfile
            if (profile == null || !profile.isConfigured) {
                operationNotice = "Connection settings are unavailable"
                return
            }
            mutationSessionId = sessionId
            operationNotice = if (status == "archived") "Archiving…" else "Restoring…"
            scope.launch {
                suspendRunCatching {
                    withContext(Dispatchers.IO) {
                        val enrolled = ensureEnrolled(profile)
                        SessionApi.updateStatus(
                            enrolled.controlUrl,
                            enrolled.deviceCredential,
                            sessionId,
                            status,
                        )
                    }
                }.onSuccess {
                    homeState = sessionHomeSessionRemoved(homeState, sessionId)
                    operationNotice = if (status == "archived") {
                        "Conversation archived"
                    } else {
                        "Conversation restored"
                    }
                    if (current && status == "archived") {
                        command(VoiceSessionService.ACTION_STOP)
                        SessionModel.state(VoiceState.IDLE)
                    }
                    refreshKey += 1
                }.onFailure { error ->
                    operationNotice = if (status == "archived") {
                        "Archive failed: ${error.message ?: "unknown error"}"
                    } else {
                        "Restore failed: ${error.message ?: "unknown error"}"
                    }
                    if (!current) homeState = sessionHomeFailure(homeState, error)
                    if (error is ControlApiException && error.statusCode == 401 && profile.isEnrolled) {
                        withContext(Dispatchers.IO) { profiles.invalidateEnrollment() }
                        settingsError =
                            "Device credential was rejected. Enter the bootstrap token to re-enroll."
                    }
                }
                mutationSessionId = null
            }
        }

        LaunchedEffect(grantedAction) {
            val action = grantedAction ?: return@LaunchedEffect
            // This value is the effect key. Consume it only after every suspending handoff;
            // clearing it here cancels this coroutine before credentials can be returned.
            val profile = storedProfile
            if (profile == null || !profile.isConfigured) {
                editing = true
                settingsError = profile?.validationError() ?: "Settings are still loading"
                grantedAction = null
                return@LaunchedEffect
            }
            SessionModel.prepare((action as? SessionAction.Open)?.sessionId.orEmpty())
            SessionModel.state(VoiceState.CONNECTING)
            suspendRunCatching {
                withContext(Dispatchers.IO) {
                    val enrolledProfile = ensureEnrolled(profile)
                    when (action) {
                        SessionAction.Create -> {
                            val idempotencyKey = profiles.beginSessionCreation()
                            val credentials = SessionApi.createGenerated(
                                enrolledProfile.controlUrl,
                                enrolledProfile.deviceCredential,
                                idempotencyKey,
                            )
                            Triple(credentials, emptyList(), credentials.roomName)
                        }
                        is SessionAction.Open -> {
                            val timeline = SessionApi.timeline(
                                enrolledProfile.controlUrl,
                                enrolledProfile.deviceCredential,
                                action.sessionId,
                            )
                            val credentials = SessionApi.reopen(
                                enrolledProfile.controlUrl,
                                enrolledProfile.deviceCredential,
                                action.sessionId,
                            )
                            Triple(credentials, timeline, action.sessionId)
                        }
                    }
                }
            }.onSuccess { (credentials, timeline, sessionId) ->
                SessionModel.open(sessionId, timeline)
                runCatching { startVoiceService(credentials) }
                    .onSuccess {
                        scope.launch {
                            suspendRunCatching {
                                withContext(Dispatchers.IO) {
                                    profiles.selectSession(sessionId)
                                }
                            }.onFailure { error ->
                                SessionModel.message(
                                    DisplayMessage(
                                        "selection-persistence-error",
                                        MessageKind.SYSTEM,
                                        "Conversation connected, but its selection could not be saved: " +
                                            (error.message ?: "local preference error"),
                                    ),
                                )
                            }
                        }
                    }
                    .onFailure { error ->
                        SessionModel.state(VoiceState.IDLE)
                        homeState = sessionHomeFailure(homeState, error)
                    }
            }.onFailure { error ->
                SessionModel.state(VoiceState.IDLE)
                homeState = sessionHomeFailure(homeState, error)
                if (error is ControlApiException && error.statusCode == 401 && profile.isEnrolled) {
                    scope.launch {
                        withContext(Dispatchers.IO) { profiles.invalidateEnrollment() }
                        settingsError =
                            "Device credential was rejected. Enter the bootstrap token to re-enroll."
                    }
                }
            }
            if (grantedAction == action) grantedAction = null
        }

        fun requestSession(action: SessionAction) {
            if (ContextCompat.checkSelfPermission(
                    this@MainActivity,
                    Manifest.permission.RECORD_AUDIO,
                ) == PackageManager.PERMISSION_GRANTED
            ) {
                grantedAction = action
            } else {
                permissionAction = action
                permission.launch(Manifest.permission.RECORD_AUDIO)
            }
        }

        Column(
            modifier = Modifier.fillMaxSize().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Radcliffe", style = MaterialTheme.typography.headlineLarge)
            Text(state.name.lowercase(), color = MaterialTheme.colorScheme.primary)
            val profile = storedProfile
            when {
                profile == null -> Text("Loading saved settings…")
                editing -> ConnectionSettings(
                    profile = profile,
                    controlUrl = controlUrl,
                    bootstrapToken = bootstrapToken,
                    settingsError = settingsError,
                    settingsNotice = settingsNotice,
                    onControlUrl = { controlUrl = it; settingsError = null; settingsNotice = null },
                    onBootstrapToken = {
                        bootstrapToken = it
                        settingsError = null
                        settingsNotice = null
                    },
                    onSave = {
                        val candidate = ConnectionProfile(
                            controlUrl,
                            bootstrapToken,
                            profile.sessionId,
                        )
                        val validation = candidate.validationError()
                        settingsNotice = null
                        if (validation != null) {
                            settingsError = validation
                        } else {
                            scope.launch {
                                suspendRunCatching {
                                    withContext(Dispatchers.IO) { profiles.save(candidate) }
                                }
                                    .onSuccess {
                                        settingsError = null
                                        editing = false
                                        refreshKey += 1
                                    }
                                    .onFailure { settingsError = "Could not save settings" }
                            }
                        }
                    },
                    onTest = {
                        val candidate = ConnectionProfile(controlUrl, bootstrapToken, profile.sessionId)
                            .normalized()
                        val validation = candidate.validationError()
                        if (validation != null) {
                            settingsError = validation
                            settingsNotice = null
                        } else {
                            settingsError = null
                            settingsNotice = "Testing…"
                            scope.launch {
                                suspendRunCatching {
                                    withContext(Dispatchers.IO) {
                                        profiles.save(candidate)
                                        val enrolled = ensureEnrolled(candidate)
                                        SessionApi.testConnection(
                                            enrolled.controlUrl,
                                            enrolled.deviceCredential,
                                        )
                                    }
                                }.onSuccess {
                                    settingsNotice = "Connection test passed"
                                }.onFailure {
                                    settingsNotice = null
                                    settingsError = it.message ?: "Connection test failed"
                                }
                            }
                        }
                    },
                    onCancel = { editing = false; settingsError = null; settingsNotice = null },
                    onClear = {
                        scope.launch {
                            suspendRunCatching {
                                withContext(Dispatchers.IO) { profiles.clear() }
                            }
                                .onSuccess {
                                    controlUrl = ""
                                    bootstrapToken = ""
                                    settingsError = null
                                    settingsNotice = null
                                    homeState = SessionHomeUiState()
                                    editing = true
                                }
                                .onFailure { settingsError = "Could not clear settings" }
                        }
                    },
                )
                state == VoiceState.IDLE -> {
                    Card(Modifier.fillMaxWidth()) {
                        Row(
                            Modifier.fillMaxWidth().padding(12.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text("Local control plane", style = MaterialTheme.typography.titleSmall)
                                Text(profile.controlUrl, style = MaterialTheme.typography.bodySmall)
                                Text(
                                    if (profile.isEnrolled) "Device enrolled securely" else "Enrollment pending",
                                    style = MaterialTheme.typography.labelSmall,
                                )
                            }
                            TextButton(onClick = {
                                controlUrl = profile.controlUrl
                                bootstrapToken = profile.bootstrapToken
                                editing = true
                            }) { Text("Settings") }
                        }
                    }
                    operationNotice?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall)
                    }
                    SessionHome(
                        state = homeState,
                        selectedSessionId = profile.sessionId,
                        archived = archivedView,
                        mutationSessionId = mutationSessionId,
                        onNewSession = { requestSession(SessionAction.Create) },
                        onOpenSession = { requestSession(SessionAction.Open(it)) },
                        onArchiveSession = {
                            updateSessionStatus(it, "archived")
                        },
                        onRestoreSession = {
                            updateSessionStatus(it, "active")
                        },
                        onShowRecent = {
                            archivedView = false
                            homeState = SessionHomeUiState(loading = true)
                            operationNotice = null
                        },
                        onShowArchived = {
                            archivedView = true
                            homeState = SessionHomeUiState(loading = true)
                            operationNotice = null
                        },
                        onRetry = { refreshKey += 1 },
                        onLoadMore = {
                            val cursor = homeState.nextCursor ?: return@SessionHome
                            if (homeState.loadingMore) return@SessionHome
                            homeState = sessionHomeLoading(homeState, more = true)
                            scope.launch {
                                suspendRunCatching {
                                    withContext(Dispatchers.IO) {
                                        loadSessionPage(
                                            profile,
                                            cursor,
                                            archived = archivedView,
                                        )
                                    }
                                }.onSuccess { loaded ->
                                    homeState = sessionHomePageAppended(
                                        homeState,
                                        loaded.items,
                                        loaded.nextCursor,
                                    )
                                }.onFailure { error ->
                                    homeState = sessionHomeFailure(homeState, error)
                                }
                            }
                        },
                        modifier = Modifier.weight(1f),
                    )
                }
                else -> ConversationScreen(
                    state = state,
                    messages = messages,
                    sessionId = activeSessionId,
                    showSystemMessages = showSystemMessages,
                    diagnosticsVisible = diagnosticsVisible,
                    mutationInFlight = mutationSessionId == activeSessionId,
                    exportInFlight = exportInFlight,
                    operationNotice = operationNotice,
                    exportNotice = exportNotice,
                    onDiagnostics = { diagnosticsVisible = !diagnosticsVisible },
                    onShowSystemMessages = { show ->
                        scope.launch {
                            suspendRunCatching {
                                withContext(Dispatchers.IO) {
                                    profiles.setShowSystemMessages(show)
                                }
                            }.onFailure { error ->
                                operationNotice =
                                    "Could not save message visibility: ${error.message ?: "unknown error"}"
                            }
                        }
                    },
                    onExport = { beginExport(activeSessionId) },
                    onArchive = {
                        updateSessionStatus(activeSessionId, "archived", current = true)
                    },
                    onCommand = ::command,
                )
            }
        }
    }

    @Composable
    private fun ConnectionSettings(
        profile: ConnectionProfile,
        controlUrl: String,
        bootstrapToken: String,
        settingsError: String?,
        settingsNotice: String?,
        onControlUrl: (String) -> Unit,
        onBootstrapToken: (String) -> Unit,
        onSave: () -> Unit,
        onTest: () -> Unit,
        onCancel: () -> Unit,
        onClear: () -> Unit,
    ) {
        Text(if (profile.isConfigured) "Connection settings" else "Set up this device")
        OutlinedTextField(
            controlUrl,
            onControlUrl,
            label = { Text("Control URL") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        OutlinedTextField(
            bootstrapToken,
            onBootstrapToken,
            label = { Text("Bootstrap token") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
        )
        settingsError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        settingsNotice?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onSave) { Text("Save") }
            OutlinedButton(onClick = onTest) { Text("Test") }
            if (profile.isConfigured) {
                OutlinedButton(onClick = onCancel) { Text("Cancel") }
            }
        }
        if (profile.isConfigured) {
            TextButton(onClick = onClear) { Text("Clear saved settings") }
        }
    }

    @Composable
    private fun ColumnScope.ConversationScreen(
        state: VoiceState,
        messages: List<DisplayMessage>,
        sessionId: String,
        showSystemMessages: Boolean,
        diagnosticsVisible: Boolean,
        mutationInFlight: Boolean,
        exportInFlight: Boolean,
        operationNotice: String?,
        exportNotice: String?,
        onDiagnostics: () -> Unit,
        onShowSystemMessages: (Boolean) -> Unit,
        onExport: () -> Unit,
        onArchive: () -> Unit,
        onCommand: (String) -> Unit,
    ) {
        var confirmArchive by remember(sessionId) { mutableStateOf(false) }
        val visibleMessages = visibleConversationMessages(messages, showSystemMessages)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { onCommand(VoiceSessionService.ACTION_INTERRUPT) },
                enabled = state !in setOf(
                    VoiceState.CONNECTING,
                    VoiceState.RECONNECTING,
                    VoiceState.FAILED,
                ),
            ) { Text("Interrupt") }
            Button(
                onClick = {
                    onCommand(
                        if (state == VoiceState.PAUSED) {
                            VoiceSessionService.ACTION_RESUME
                        } else {
                            VoiceSessionService.ACTION_PAUSE
                        },
                    )
                },
                enabled = state !in setOf(
                    VoiceState.CONNECTING,
                    VoiceState.RECONNECTING,
                    VoiceState.FAILED,
                ),
            ) { Text(if (state == VoiceState.PAUSED) "Resume" else "Pause") }
            Button(onClick = { onCommand(VoiceSessionService.ACTION_STOP) }) { Text("End") }
        }
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column {
                Text("Show system messages", style = MaterialTheme.typography.bodySmall)
                Text(
                    "Protocol notices remain stored when hidden",
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            Switch(
                checked = showSystemMessages,
                onCheckedChange = onShowSystemMessages,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onExport, enabled = !exportInFlight) {
                Text(if (exportInFlight) "Exporting…" else "Export")
            }
            TextButton(onClick = { confirmArchive = true }, enabled = !mutationInFlight) {
                Text(if (mutationInFlight) "Archiving…" else "Archive")
            }
            TextButton(onClick = onDiagnostics) {
                Text(if (diagnosticsVisible) "Hide diagnostics" else "Diagnostics")
            }
        }
        operationNotice?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        exportNotice?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        if (diagnosticsVisible) {
            Text("Session ID: $sessionId", style = MaterialTheme.typography.labelSmall)
            Text(
                "${messages.size} durable/live messages · ${visibleMessages.size} shown",
                style = MaterialTheme.typography.labelSmall,
            )
        }
        LazyColumn(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(visibleMessages, key = { it.id }) { message -> MessageCard(message) }
        }
        if (confirmArchive) {
            AlertDialog(
                onDismissRequest = { confirmArchive = false },
                title = { Text("Archive this conversation?") },
                text = {
                    Text(
                        "The voice session will end. Its durable messages remain available " +
                            "from Archived conversations.",
                    )
                },
                confirmButton = {
                    Button(
                        onClick = {
                            confirmArchive = false
                            onArchive()
                        },
                    ) { Text("Archive") }
                },
                dismissButton = {
                    TextButton(onClick = { confirmArchive = false }) { Text("Cancel") }
                },
            )
        }
    }

    @Composable
    private fun MessageCard(message: DisplayMessage) {
        val (containerColor, contentColor) = when (message.kind) {
            MessageKind.USER -> MaterialTheme.colorScheme.secondaryContainer to
                MaterialTheme.colorScheme.onSecondaryContainer
            MessageKind.MODEL -> MaterialTheme.colorScheme.primaryContainer to
                MaterialTheme.colorScheme.onPrimaryContainer
            MessageKind.SOURCE -> MaterialTheme.colorScheme.tertiaryContainer to
                MaterialTheme.colorScheme.onTertiaryContainer
            MessageKind.SYSTEM -> MaterialTheme.colorScheme.errorContainer to
                MaterialTheme.colorScheme.onErrorContainer
        }
        val label = if (message.kind == MessageKind.SOURCE) {
            message.provenance?.let { "Source · $it" } ?: "Authorized source"
        } else {
            message.kind.name.lowercase().replaceFirstChar(Char::uppercase)
        }
        Card(
            modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {},
            colors = CardDefaults.cardColors(
                containerColor = containerColor,
                contentColor = contentColor,
            ),
        ) {
            Column(Modifier.fillMaxWidth().padding(12.dp)) {
                Text(label, style = MaterialTheme.typography.titleSmall)
                Text(message.text)
            }
        }
    }

    private suspend fun loadSessionPage(
        profile: ConnectionProfile,
        cursor: String? = null,
        archived: Boolean = false,
    ): LoadedSessionPage = coroutineScope {
        val enrolledProfile = ensureEnrolled(profile)
        val page = SessionApi.list(
            enrolledProfile.controlUrl,
            enrolledProfile.deviceCredential,
            cursor = cursor,
            status = if (archived) "archived" else null,
        )
        LoadedSessionPage(
            items = page.items.map { summary ->
                async {
                    val usage = runCatching {
                        SessionApi.contextUsage(
                            enrolledProfile.controlUrl,
                            enrolledProfile.deviceCredential,
                            summary.sessionId,
                        )
                    }.fold(
                        onSuccess = { ContextUsageState.Available(it) },
                        onFailure = { ContextUsageState.Unavailable },
                    )
                    RecentSessionItem(summary, usage)
                }
            }.awaitAll(),
            nextCursor = page.nextCursor,
        )
    }

    private suspend fun ensureEnrolled(profile: ConnectionProfile): ConnectionProfile =
        enrollmentMutex.withLock {
            val current = profiles.profile.first()
            if (current.isEnrolled && current.controlUrl == profile.controlUrl) return@withLock current
            val prepared = profiles.prepareEnrollment()
            require(prepared.bootstrapToken.isNotBlank()) { "Bootstrap token is required to enroll" }
            val enrollment = SessionApi.enroll(
                prepared.controlUrl,
                prepared.bootstrapToken,
                prepared.installationId,
                android.os.Build.MODEL.ifBlank { "Android device" },
                prepared.deviceCredential,
            )
            profiles.completeEnrollment(enrollment.deviceId, enrollment.participantIdentity)
            profiles.profile.first()
        }

    private fun startVoiceService(credentials: SessionCredentials) {
        val intent = Intent(this, VoiceSessionService::class.java)
            .setAction(VoiceSessionService.ACTION_START)
            .putExtra(VoiceSessionService.EXTRA_URL, credentials.liveKitUrl)
            .putExtra(VoiceSessionService.EXTRA_SESSION_ID, credentials.roomName)
            .putExtra(VoiceSessionService.EXTRA_TOKEN, credentials.participantToken)
        ContextCompat.startForegroundService(this, intent)
    }

    private fun command(action: String) {
        startService(Intent(this, VoiceSessionService::class.java).setAction(action))
    }
}
