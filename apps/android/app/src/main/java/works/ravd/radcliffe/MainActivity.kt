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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private sealed interface SessionAction {
    data object Create : SessionAction
    data class Open(val sessionId: String) : SessionAction
}

private data class LoadedSessionPage(
    val items: List<RecentSessionItem>,
    val nextCursor: String?,
)

class MainActivity : ComponentActivity() {
    private val profiles by lazy { ConnectionProfileRepository.create(this) }

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

        LaunchedEffect(storedProfile) {
            val profile = storedProfile ?: return@LaunchedEffect
            if (!initialized || !editing) {
                controlUrl = profile.controlUrl
                bootstrapToken = profile.bootstrapToken
                editing = !profile.isConfigured
                initialized = true
            }
        }

        LaunchedEffect(storedProfile?.controlUrl, storedProfile?.bootstrapToken, refreshKey) {
            val profile = storedProfile ?: return@LaunchedEffect
            if (!profile.isConfigured || editing || state != VoiceState.IDLE) return@LaunchedEffect
            homeState = sessionHomeLoading(homeState)
            runCatching { withContext(Dispatchers.IO) { loadSessionPage(profile) } }
                .onSuccess { loaded ->
                    homeState = sessionHomeLoaded(loaded.items, loaded.nextCursor)
                }
                .onFailure { error -> homeState = sessionHomeFailure(homeState, error) }
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

        LaunchedEffect(grantedAction) {
            val action = grantedAction ?: return@LaunchedEffect
            grantedAction = null
            val profile = storedProfile
            if (profile == null || !profile.isConfigured) {
                editing = true
                settingsError = profile?.validationError() ?: "Settings are still loading"
                return@LaunchedEffect
            }
            SessionModel.prepare((action as? SessionAction.Open)?.sessionId.orEmpty())
            SessionModel.state(VoiceState.CONNECTING)
            runCatching {
                withContext(Dispatchers.IO) {
                    when (action) {
                        SessionAction.Create -> {
                            val idempotencyKey = profiles.beginSessionCreation()
                            val credentials = SessionApi.createGenerated(
                                profile.controlUrl,
                                profile.bootstrapToken,
                                idempotencyKey,
                            )
                            Triple(credentials, emptyList(), credentials.roomName)
                        }
                        is SessionAction.Open -> {
                            val timeline = SessionApi.timeline(
                                profile.controlUrl,
                                profile.bootstrapToken,
                                action.sessionId,
                            )
                            val credentials = SessionApi.reopen(
                                profile.controlUrl,
                                profile.bootstrapToken,
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
                            runCatching {
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
            }
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
                                runCatching { withContext(Dispatchers.IO) { profiles.save(candidate) } }
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
                                runCatching {
                                    withContext(Dispatchers.IO) {
                                        SessionApi.testConnection(
                                            candidate.controlUrl,
                                            candidate.bootstrapToken,
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
                            runCatching { withContext(Dispatchers.IO) { profiles.clear() } }
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
                                Text("Credential saved securely", style = MaterialTheme.typography.labelSmall)
                            }
                            TextButton(onClick = {
                                controlUrl = profile.controlUrl
                                bootstrapToken = profile.bootstrapToken
                                editing = true
                            }) { Text("Settings") }
                        }
                    }
                    SessionHome(
                        state = homeState,
                        selectedSessionId = profile.sessionId,
                        onNewSession = { requestSession(SessionAction.Create) },
                        onOpenSession = { requestSession(SessionAction.Open(it)) },
                        onRetry = { refreshKey += 1 },
                        onLoadMore = {
                            val cursor = homeState.nextCursor ?: return@SessionHome
                            if (homeState.loadingMore) return@SessionHome
                            homeState = sessionHomeLoading(homeState, more = true)
                            scope.launch {
                                runCatching {
                                    withContext(Dispatchers.IO) { loadSessionPage(profile, cursor) }
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
                    diagnosticsVisible = diagnosticsVisible,
                    onDiagnostics = { diagnosticsVisible = !diagnosticsVisible },
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
        diagnosticsVisible: Boolean,
        onDiagnostics: () -> Unit,
        onCommand: (String) -> Unit,
    ) {
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
        TextButton(onClick = onDiagnostics) {
            Text(if (diagnosticsVisible) "Hide diagnostics" else "Diagnostics")
        }
        if (diagnosticsVisible) {
            Text("Session ID: $sessionId", style = MaterialTheme.typography.labelSmall)
        }
        LazyColumn(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(messages, key = { it.id }) { message -> MessageCard(message) }
        }
    }

    @Composable
    private fun MessageCard(message: DisplayMessage) {
        val color = when (message.kind) {
            MessageKind.USER -> MaterialTheme.colorScheme.secondaryContainer
            MessageKind.MODEL -> MaterialTheme.colorScheme.primaryContainer
            MessageKind.SOURCE -> MaterialTheme.colorScheme.tertiaryContainer
            MessageKind.SYSTEM -> MaterialTheme.colorScheme.errorContainer
        }
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.fillMaxWidth().padding(12.dp)) {
                Text(message.kind.name.lowercase(), color = color)
                message.provenance?.let { Text(it, style = MaterialTheme.typography.labelSmall) }
                Text(message.text)
            }
        }
    }

    private suspend fun loadSessionPage(
        profile: ConnectionProfile,
        cursor: String? = null,
    ): LoadedSessionPage = coroutineScope {
        val page = SessionApi.list(
            profile.controlUrl,
            profile.bootstrapToken,
            cursor = cursor,
        )
        LoadedSessionPage(
            items = page.items.map { summary ->
                async {
                    val usage = runCatching {
                        SessionApi.contextUsage(
                            profile.controlUrl,
                            profile.bootstrapToken,
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
