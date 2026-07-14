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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

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
        val storedProfile by profiles.profile.collectAsState(initial = null)
        val scope = rememberCoroutineScope()
        var editing by remember { mutableStateOf(false) }
        var initialized by remember { mutableStateOf(false) }
        var controlUrl by remember { mutableStateOf("") }
        var bootstrapToken by remember { mutableStateOf("") }
        var sessionId by remember { mutableStateOf(ConnectionProfile.DEFAULT_SESSION_ID) }
        var settingsError by remember { mutableStateOf<String?>(null) }
        var settingsNotice by remember { mutableStateOf<String?>(null) }
        var pendingProfile by remember { mutableStateOf<ConnectionProfile?>(null) }
        LaunchedEffect(storedProfile) {
            val profile = storedProfile ?: return@LaunchedEffect
            if (!initialized || !editing) {
                controlUrl = profile.controlUrl
                bootstrapToken = profile.bootstrapToken
                sessionId = profile.sessionId
                editing = !profile.isConfigured
                initialized = true
            }
        }
        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
            val profile = pendingProfile
            pendingProfile = null
            if (it && profile != null) connect(profile)
        }
        Column(
            modifier = Modifier.fillMaxSize().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Radcliffe", style = MaterialTheme.typography.headlineLarge)
            Text(state.name.lowercase(), color = MaterialTheme.colorScheme.primary)
            val profile = storedProfile
            if (profile == null) {
                Text("Loading saved settings…")
            } else if (editing) {
                Text(if (profile.isConfigured) "Connection settings" else "Set up this device")
                OutlinedTextField(
                    controlUrl,
                    { controlUrl = it; settingsError = null; settingsNotice = null },
                    label = { Text("Control URL") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                OutlinedTextField(
                    bootstrapToken,
                    { bootstrapToken = it; settingsError = null; settingsNotice = null },
                    label = { Text("Bootstrap token") },
                    modifier = Modifier.fillMaxWidth(),
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                )
                OutlinedTextField(
                    sessionId,
                    { sessionId = it; settingsError = null; settingsNotice = null },
                    label = { Text("Session") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                settingsError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                settingsNotice?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = {
                        val candidate = ConnectionProfile(controlUrl, bootstrapToken, sessionId)
                        val validation = candidate.validationError()
                        settingsNotice = null
                        if (validation != null) {
                            settingsError = validation
                        } else {
                            scope.launch {
                                runCatching {
                                    withContext(Dispatchers.IO) { profiles.save(candidate) }
                                }.onSuccess {
                                    settingsError = null
                                    editing = false
                                }.onFailure {
                                    settingsError = "Could not save settings"
                                }
                            }
                        }
                    }) { Text("Save") }
                    OutlinedButton(onClick = {
                        val candidate = ConnectionProfile(controlUrl, bootstrapToken, sessionId).normalized()
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
                                        SessionApi.create(
                                            candidate.controlUrl,
                                            candidate.bootstrapToken,
                                            candidate.sessionId,
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
                    }) { Text("Test") }
                    if (profile.isConfigured) {
                        OutlinedButton(onClick = {
                            settingsError = null
                            settingsNotice = null
                            editing = false
                        }) { Text("Cancel") }
                    }
                }
                if (profile.isConfigured) {
                    TextButton(onClick = {
                        scope.launch {
                            runCatching {
                                withContext(Dispatchers.IO) { profiles.clear() }
                            }.onSuccess {
                                controlUrl = ""
                                bootstrapToken = ""
                                sessionId = ConnectionProfile.DEFAULT_SESSION_ID
                                settingsError = null
                                settingsNotice = null
                                editing = true
                            }.onFailure {
                                settingsError = "Could not clear settings"
                            }
                        }
                    }) { Text("Clear saved settings") }
                }
            } else {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.fillMaxWidth().padding(12.dp)) {
                        Text(profile.controlUrl)
                        Text("Session: ${profile.sessionId}")
                        Text("Bootstrap token saved securely")
                    }
                }
                OutlinedButton(
                    onClick = {
                        controlUrl = profile.controlUrl
                        bootstrapToken = profile.bootstrapToken
                        sessionId = profile.sessionId
                        editing = true
                    },
                    enabled = state == VoiceState.IDLE || state == VoiceState.FAILED,
                ) { Text("Change settings") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    val ready = storedProfile
                    if (ready == null || !ready.isConfigured) {
                        editing = true
                        settingsError = ready?.validationError() ?: "Settings are still loading"
                    } else if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                        connect(ready)
                    } else {
                        pendingProfile = ready
                        permission.launch(Manifest.permission.RECORD_AUDIO)
                    }
                }, enabled = (state == VoiceState.IDLE || state == VoiceState.FAILED) && !editing) { Text("Start") }
                Button(onClick = { command(VoiceSessionService.ACTION_INTERRUPT) }, enabled = state != VoiceState.IDLE) { Text("Interrupt") }
                Button(onClick = { command(if (state == VoiceState.PAUSED) VoiceSessionService.ACTION_RESUME else VoiceSessionService.ACTION_PAUSE) }, enabled = state != VoiceState.IDLE) { Text(if (state == VoiceState.PAUSED) "Resume" else "Pause") }
                Button(onClick = { command(VoiceSessionService.ACTION_STOP) }, enabled = state != VoiceState.IDLE) { Text("End") }
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(messages, key = { it.id }) { message -> MessageCard(message) }
            }
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

    private fun connect(profile: ConnectionProfile) {
        SessionModel.state(VoiceState.CONNECTING)
        lifecycleScope.launch {
            runCatching {
                withContext(Dispatchers.IO) {
                    SessionApi.create(
                        profile.controlUrl,
                        profile.bootstrapToken,
                        profile.sessionId,
                    )
                }
            }.onSuccess { credentials ->
                val intent = Intent(this@MainActivity, VoiceSessionService::class.java)
                    .setAction(VoiceSessionService.ACTION_START)
                    .putExtra(VoiceSessionService.EXTRA_URL, credentials.liveKitUrl)
                    .putExtra(VoiceSessionService.EXTRA_SESSION_ID, credentials.roomName)
                    .putExtra(VoiceSessionService.EXTRA_TOKEN, credentials.participantToken)
                ContextCompat.startForegroundService(this@MainActivity, intent)
            }.onFailure {
                SessionModel.state(VoiceState.FAILED)
                SessionModel.message(DisplayMessage("control-error-${System.nanoTime()}", MessageKind.SYSTEM, it.message ?: "Session failed"))
            }
        }
    }

    private fun command(action: String) {
        startService(Intent(this, VoiceSessionService::class.java).setAction(action))
    }
}
