package works.ravd.radcliffe

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import io.livekit.android.LiveKit
import io.livekit.android.events.RoomEvent
import io.livekit.android.room.Room
import io.livekit.android.room.track.DataPublishReliability
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import works.ravd.radcliffe.protocol.v2.ClientEvent
import works.ravd.radcliffe.protocol.v2.ControlCommand
import works.ravd.radcliffe.protocol.v2.ConversationState
import works.ravd.radcliffe.protocol.v2.OperationRef
import works.ravd.radcliffe.protocol.v2.ServerEvent
import works.ravd.radcliffe.protocol.v2.SourceKind

class VoiceSessionService : MediaSessionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var room: Room? = null
    private lateinit var player: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private var sessionId: String = ""

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Voice session", NotificationManager.IMPORTANCE_LOW),
        )
        player = ExoPlayer.Builder(this).build()
        mediaSession = MediaSession.Builder(this, player).build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = mediaSession

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_START -> startSession(intent)
            ACTION_INTERRUPT -> interrupt()
            ACTION_PAUSE -> setPaused(true)
            ACTION_RESUME -> setPaused(false)
            ACTION_STOP -> stopSession()
        }
        return START_NOT_STICKY
    }

    private fun startSession(intent: Intent) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            SessionModel.state(VoiceState.FAILED)
            stopSelf()
            return
        }
        val url = intent.getStringExtra(EXTRA_URL) ?: return
        val token = intent.getStringExtra(EXTRA_TOKEN) ?: return
        sessionId = intent.getStringExtra(EXTRA_SESSION_ID) ?: return
        startForeground(NOTIFICATION_ID, notification("Connecting", false))
        SessionModel.state(VoiceState.CONNECTING)
        scope.launch {
            runCatching {
                LiveKit.create(this@VoiceSessionService).also {
                    room = it
                    observe(it)
                    it.connect(url, token)
                    it.localParticipant.setMicrophoneEnabled(true)
                }
            }.onSuccess {
                SessionModel.state(VoiceState.LISTENING)
                notify("Listening", false)
            }.onFailure {
                SessionModel.state(VoiceState.FAILED)
                SessionModel.message(DisplayMessage("connection-error", MessageKind.SYSTEM, it.message ?: "Connection failed"))
                stopSession()
            }
        }
    }

    private fun interrupt() {
        room?.setSpeakerMute(true)
        room?.setSpeakerMute(false)
        SessionModel.state(VoiceState.LISTENING)
        sendControl(ControlCommand.Kind.KIND_INTERRUPT)
    }

    private fun setPaused(paused: Boolean) {
        room?.setSpeakerMute(paused)
        if (paused) player.pause() else player.play()
        SessionModel.state(if (paused) VoiceState.PAUSED else VoiceState.LISTENING)
        notify(if (paused) "Paused" else "Listening", paused)
        sendControl(if (paused) ControlCommand.Kind.KIND_PAUSE else ControlCommand.Kind.KIND_RESUME)
    }

    private fun sendControl(kind: ControlCommand.Kind) {
        val activeRoom = room ?: return
        val operation = OperationRef.newBuilder()
            .setSessionId(sessionId)
            .setGeneration(1)
            .setOperationId("android-control")
            .setCancellationId("android-control")
            .build()
        val command = ControlCommand.newBuilder()
            .setCommandId(java.util.UUID.randomUUID().toString())
            .setKind(kind)
            .setOperation(operation)
            .setIdempotencyKey(java.util.UUID.randomUUID().toString())
            .build()
        val event = ClientEvent.newBuilder().setProtocolVersion(2).setControlCommand(command).build()
        scope.launch {
            activeRoom.localParticipant.publishData(event.toByteArray(), DataPublishReliability.RELIABLE)
        }
    }

    private fun observe(activeRoom: Room) {
        scope.launch {
            activeRoom.events.events.collect { event ->
                when (event) {
                    is RoomEvent.Reconnecting -> SessionModel.state(VoiceState.RECONNECTING)
                    is RoomEvent.Reconnected -> SessionModel.state(VoiceState.LISTENING)
                    is RoomEvent.DataReceived -> runCatching { ServerEvent.parseFrom(event.data) }
                        .onSuccess(::handleServerEvent)
                    else -> Unit
                }
            }
        }
    }

    private fun handleServerEvent(event: ServerEvent) {
        if (event.protocolVersion != 2) return
        if (event.hasStateChanged()) {
            SessionModel.state(when (event.stateChanged.current) {
                ConversationState.CONVERSATION_STATE_CONNECTING -> VoiceState.CONNECTING
                ConversationState.CONVERSATION_STATE_LISTENING -> VoiceState.LISTENING
                ConversationState.CONVERSATION_STATE_THINKING -> VoiceState.THINKING
                ConversationState.CONVERSATION_STATE_SPEAKING -> VoiceState.SPEAKING
                ConversationState.CONVERSATION_STATE_READING -> VoiceState.READING
                ConversationState.CONVERSATION_STATE_PAUSED -> VoiceState.PAUSED
                ConversationState.CONVERSATION_STATE_RECONNECTING -> VoiceState.RECONNECTING
                ConversationState.CONVERSATION_STATE_FAILED -> VoiceState.FAILED
                else -> VoiceState.IDLE
            })
        }
        if (event.hasMessage() && event.message.content.hasInlineText()) {
            val message = event.message
            val kind = when (message.source) {
                SourceKind.SOURCE_KIND_USER -> MessageKind.USER
                SourceKind.SOURCE_KIND_MODEL -> MessageKind.MODEL
                SourceKind.SOURCE_KIND_TEXT_SOURCE -> MessageKind.SOURCE
                else -> MessageKind.SYSTEM
            }
            SessionModel.message(
                DisplayMessage(
                    message.messageId,
                    kind,
                    message.content.inlineText,
                    message.provenance.displayName.ifBlank { null },
                ),
            )
        }
    }

    private fun stopSession() {
        room?.disconnect()
        room?.release()
        room = null
        SessionModel.state(VoiceState.IDLE)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun notification(label: String, paused: Boolean): Notification {
        val activity = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val pauseAction = if (paused) ACTION_RESUME else ACTION_PAUSE
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Radcliffe")
            .setContentText(label)
            .setContentIntent(activity)
            .setOngoing(true)
            .addAction(0, "Interrupt", action(ACTION_INTERRUPT, 1))
            .addAction(0, if (paused) "Resume" else "Pause", action(pauseAction, 2))
            .addAction(0, "Stop", action(ACTION_STOP, 3))
            .build()
    }

    private fun action(value: String, code: Int): PendingIntent = PendingIntent.getService(
        this, code, Intent(this, VoiceSessionService::class.java).setAction(value),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun notify(label: String, paused: Boolean) {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(label, paused))
    }

    override fun onDestroy() {
        room?.disconnect()
        room?.release()
        mediaSession.release()
        player.release()
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val ACTION_START = "works.ravd.radcliffe.START"
        const val ACTION_INTERRUPT = "works.ravd.radcliffe.INTERRUPT"
        const val ACTION_PAUSE = "works.ravd.radcliffe.PAUSE"
        const val ACTION_RESUME = "works.ravd.radcliffe.RESUME"
        const val ACTION_STOP = "works.ravd.radcliffe.STOP"
        const val EXTRA_URL = "livekit_url"
        const val EXTRA_TOKEN = "participant_token"
        const val EXTRA_SESSION_ID = "session_id"
        private const val CHANNEL_ID = "voice-session"
        private const val NOTIFICATION_ID = 1001
    }
}
