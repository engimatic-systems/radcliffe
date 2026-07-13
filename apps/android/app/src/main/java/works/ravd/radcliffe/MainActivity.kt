package works.ravd.radcliffe

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { RadcliffeScreen() } } }
    }

    @Composable
    private fun RadcliffeScreen() {
        val state by SessionModel.state.collectAsState()
        val messages by SessionModel.messages.collectAsState()
        var controlUrl by remember { mutableStateOf("https://radcliffe.example.com") }
        var bootstrapToken by remember { mutableStateOf("") }
        var sessionId by remember { mutableStateOf("radcliffe-${UUID.randomUUID().toString().take(8)}") }
        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
            if (it) connect(controlUrl, bootstrapToken, sessionId)
        }
        Column(
            modifier = Modifier.fillMaxSize().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Radcliffe", style = MaterialTheme.typography.headlineLarge)
            Text(state.name.lowercase(), color = MaterialTheme.colorScheme.primary)
            OutlinedTextField(controlUrl, { controlUrl = it }, label = { Text("Control URL") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(bootstrapToken, { bootstrapToken = it }, label = { Text("Bootstrap token") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(sessionId, { sessionId = it }, label = { Text("Session") }, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                        connect(controlUrl, bootstrapToken, sessionId)
                    } else permission.launch(Manifest.permission.RECORD_AUDIO)
                }, enabled = state == VoiceState.IDLE || state == VoiceState.FAILED) { Text("Start") }
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

    private fun connect(controlUrl: String, bootstrapToken: String, sessionId: String) {
        SessionModel.state(VoiceState.CONNECTING)
        lifecycleScope.launch {
            runCatching {
                withContext(Dispatchers.IO) { SessionApi.create(controlUrl, bootstrapToken, sessionId) }
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
