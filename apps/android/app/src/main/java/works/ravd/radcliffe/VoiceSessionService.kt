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
import io.livekit.android.room.track.RemoteAudioTrack
import io.livekit.android.room.track.DataPublishReliability
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import works.ravd.radcliffe.protocol.v2.ClientEvent
import works.ravd.radcliffe.protocol.v2.AgentControlActionId
import works.ravd.radcliffe.protocol.v2.AgentControlConfirmation
import works.ravd.radcliffe.protocol.v2.AgentControlConfirmationRequirement
import works.ravd.radcliffe.protocol.v2.AgentControlOrigin
import works.ravd.radcliffe.protocol.v2.ControlCommand
import works.ravd.radcliffe.protocol.v2.ConversationState
import works.ravd.radcliffe.protocol.v2.OperationRef
import works.ravd.radcliffe.protocol.v2.PlaybackAcknowledgement
import works.ravd.radcliffe.protocol.v2.ServerEvent
import works.ravd.radcliffe.protocol.v2.SourceKind
import livekit.org.webrtc.AudioTrackSink

class VoiceSessionService : MediaSessionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val profiles by lazy { ConnectionProfileRepository.create(this) }
    private var room: Room? = null
    private lateinit var player: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private var sessionId: String = ""
    private val playbackTracker = DevicePlaybackTracker()
    private val playbackSinks = mutableMapOf<RemoteAudioTrack, AudioTrackSink>()
    private val handledTransitions = mutableSetOf<String>()
    private var playbackPaused = false
    private var playbackSuppressedForInterruption = false

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
            ACTION_STOP -> requestStop()
            ACTION_CONFIRM_AGENT_CONTROL -> respondToAgentControl(intent, true)
            ACTION_REJECT_AGENT_CONTROL -> respondToAgentControl(intent, false)
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
        val requestedSessionId = intent.getStringExtra(EXTRA_SESSION_ID) ?: return
        startForeground(NOTIFICATION_ID, notification("Connecting", false))
        SessionModel.state(VoiceState.CONNECTING)
        scope.launch {
            runCatching {
                connectRoom(url, token, requestedSessionId)
            }.onSuccess {
                notify("Waiting for microphone", false)
            }.onFailure {
                SessionModel.state(VoiceState.FAILED)
                SessionModel.message(DisplayMessage("connection-error", MessageKind.SYSTEM, it.message ?: "Connection failed"))
                stopSession(resetState = false)
            }
        }
    }

    private suspend fun connectRoom(url: String, token: String, destinationSessionId: String) {
        val connected = LiveKit.create(this@VoiceSessionService)
        room = connected
        sessionId = destinationSessionId
        observe(connected)
        connected.connect(url, token)
        connected.localParticipant.setMicrophoneEnabled(true)
    }

    private fun interrupt() {
        room?.setSpeakerMute(true)
        playbackSuppressedForInterruption = true
        val playbackAcknowledgements = playbackTracker.invalidatePending("interrupted on device")
        SessionModel.state(VoiceState.LISTENING)
        sendControl(ControlCommand.Kind.KIND_INTERRUPT, playbackAcknowledgements)
    }

    private fun setPaused(paused: Boolean) {
        playbackPaused = paused
        if (!paused) playbackSuppressedForInterruption = false
        room?.setSpeakerMute(paused)
        if (paused) player.pause() else player.play()
        SessionModel.state(if (paused) VoiceState.PAUSED else VoiceState.LISTENING)
        notify(if (paused) "Paused" else "Listening", paused)
        val playbackAcknowledgements =
            if (paused) playbackTracker.flush("paused on device") else emptyList()
        sendControl(
            if (paused) ControlCommand.Kind.KIND_PAUSE else ControlCommand.Kind.KIND_RESUME,
            playbackAcknowledgements,
        )
    }

    private fun sendControl(
        kind: ControlCommand.Kind,
        playbackAcknowledgements: List<PlaybackAcknowledgement> = emptyList(),
    ) {
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
            playbackAcknowledgements.forEach { publishPlaybackAcknowledgement(activeRoom, it) }
            activeRoom.localParticipant.publishData(event.toByteArray(), DataPublishReliability.RELIABLE)
        }
    }

    private fun requestStop() {
        room?.setSpeakerMute(true)
        val activeRoom = room
        if (activeRoom == null) {
            stopSession()
            return
        }
        val playbackAcknowledgements = playbackTracker.flush("stopped on device")
        val operation = OperationRef.newBuilder()
            .setSessionId(sessionId)
            .setGeneration(1)
            .setOperationId("android-control")
            .setCancellationId("android-control")
            .build()
        val command = ControlCommand.newBuilder()
            .setCommandId(java.util.UUID.randomUUID().toString())
            .setKind(ControlCommand.Kind.KIND_STOP)
            .setOperation(operation)
            .setIdempotencyKey(java.util.UUID.randomUUID().toString())
            .build()
        val event = ClientEvent.newBuilder().setProtocolVersion(2).setControlCommand(command).build()
        scope.launch {
            runCatching {
                playbackAcknowledgements.forEach {
                    publishPlaybackAcknowledgement(activeRoom, it)
                }
                activeRoom.localParticipant.publishData(
                    event.toByteArray(),
                    DataPublishReliability.RELIABLE,
                )
            }
            stopSession()
        }
    }

    private fun observe(activeRoom: Room) {
        scope.launch {
            activeRoom.events.events.collect { event ->
                if (room !== activeRoom) return@collect
                when (event) {
                    is RoomEvent.Reconnecting -> SessionModel.state(VoiceState.RECONNECTING)
                    is RoomEvent.Reconnected -> SessionModel.state(VoiceState.CONNECTING)
                    is RoomEvent.TrackSubscribed -> attachPlaybackSink(event.track)
                    is RoomEvent.TrackUnsubscribed -> detachPlaybackSink(event.track)
                    is RoomEvent.DataReceived -> runCatching { ServerEvent.parseFrom(event.data) }
                        .onSuccess(::handleServerEvent)
                    else -> Unit
                }
            }
        }
    }

    private fun handleServerEvent(event: ServerEvent) {
        if (event.protocolVersion != 2) return
        if (event.hasPlaybackSegment()) {
            val accepted = playbackTracker.announce(event.playbackSegment)
            if (accepted && playbackSuppressedForInterruption && !playbackPaused) {
                room?.setSpeakerMute(false)
                playbackSuppressedForInterruption = false
            }
        }
        if (event.hasJobEvent()) {
            backgroundJobFromEvent(event.jobEvent, sessionId)?.let(BackgroundJobModel::update)
        }
        if (event.hasSessionReady()) {
            val current = SessionModel.state.value
            val ready = stateAfterSessionReady(sessionId, event.sessionReady.sessionId, current)
            if (ready != current) {
                SessionModel.state(ready)
                if (ready == VoiceState.LISTENING) BehaviorConfigurationModel.expireTurnOverrides()
                notify("Listening", false)
            }
        }
        if (event.hasStateChanged()) {
            if (event.stateChanged.reason == "interrupted") {
                suppressInterruptedPlayback(event.stateChanged.operation)
            }
            val current = when (event.stateChanged.current) {
                ConversationState.CONVERSATION_STATE_CONNECTING -> VoiceState.CONNECTING
                ConversationState.CONVERSATION_STATE_LISTENING -> VoiceState.LISTENING
                ConversationState.CONVERSATION_STATE_THINKING -> VoiceState.THINKING
                ConversationState.CONVERSATION_STATE_SPEAKING -> VoiceState.SPEAKING
                ConversationState.CONVERSATION_STATE_READING -> VoiceState.READING
                ConversationState.CONVERSATION_STATE_PAUSED -> VoiceState.PAUSED
                ConversationState.CONVERSATION_STATE_RECONNECTING -> VoiceState.RECONNECTING
                ConversationState.CONVERSATION_STATE_FAILED -> VoiceState.FAILED
                else -> VoiceState.IDLE
            }
            SessionModel.state(current)
            if (current == VoiceState.LISTENING) BehaviorConfigurationModel.expireTurnOverrides()
        }
        if (event.hasAgentControlRequest()) {
            val request = event.agentControlRequest
            if (
                request.operation.sessionId == sessionId &&
                request.operation.generation >= 1 &&
                request.confirmation ==
                AgentControlConfirmationRequirement.AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER
            ) {
                val pending = when (request.actionId) {
                    AgentControlActionId.AGENT_CONTROL_ACTION_ID_SESSION_SWITCH -> {
                        PendingAgentControl(
                            request,
                            "Switch conversation?",
                            "Switch to ${request.sessionSwitch.destinationSessionId}?",
                        )
                    }
                    AgentControlActionId.AGENT_CONTROL_ACTION_ID_SESSION_RESTART -> {
                        PendingAgentControl(
                            request,
                            "Restart conversation?",
                            "End this interaction and continue in a fresh conversation?",
                        )
                    }
                    AgentControlActionId.AGENT_CONTROL_ACTION_ID_SESSION_END -> {
                        PendingAgentControl(
                            request,
                            "End conversation?",
                            "End the current voice conversation?",
                        )
                    }
                    else -> null
                }
                pending?.let(AgentControlModel::request)
            }
        }
        if (event.hasMessage() && event.message.content.hasInlineText()) {
            val message = event.message
            if (message.metadataMap["radcliffe.event"] == "session_transition") {
                applySessionTransition(message.metadataMap)
            }
            if (message.metadataMap["radcliffe.event"] == "configuration_changed") {
                val key = message.metadataMap["radcliffe.configuration.key"]
                val value = message.metadataMap["radcliffe.configuration.value"]
                if (key != null && value != null) {
                    BehaviorConfigurationModel.update(
                        ConfigurationUpdate(
                            key = key,
                            value = value,
                            effectiveScope = message.metadataMap[
                                "radcliffe.configuration.scope"
                            ] ?: "built_in",
                            revision = message.metadataMap[
                                "radcliffe.configuration.revision"
                            ]?.toIntOrNull() ?: 0,
                            mutationId = message.metadataMap[
                                "radcliffe.configuration.mutation_id"
                            ],
                        ),
                    )
                }
            }
            val kind = when (message.source) {
                SourceKind.SOURCE_KIND_USER -> MessageKind.USER
                SourceKind.SOURCE_KIND_MODEL -> MessageKind.MODEL
                SourceKind.SOURCE_KIND_TEXT_SOURCE -> MessageKind.SOURCE
                else -> MessageKind.SYSTEM
            }
            SessionModel.message(
                DisplayMessage(
                    id = message.messageId,
                    kind = kind,
                    text = message.content.inlineText,
                    provenance = message.provenance.displayName.ifBlank { null },
                    deliveryLabel = message.metadataMap["radcliffe.delivery.summary"],
                    technicalDetailCategories = technicalDetailCategories(
                        kind,
                        message.metadataMap["radcliffe.spoken_output.categories"]
                            ?.split(",")
                            .orEmpty(),
                    ),
                ),
            )
        }
    }

    private fun suppressInterruptedPlayback(operation: OperationRef) {
        room?.setSpeakerMute(true)
        playbackPaused = false
        playbackSuppressedForInterruption = true
        val activeRoom = room ?: return
        val acknowledgements = playbackTracker.invalidateThrough(
            operation,
            "verified speech interrupted playback",
        )
        if (acknowledgements.isNotEmpty()) {
            scope.launch {
                acknowledgements.forEach { publishPlaybackAcknowledgement(activeRoom, it) }
            }
        }
    }

    private fun respondToAgentControl(intent: Intent, approved: Boolean) {
        val requestId = intent.getStringExtra(EXTRA_AGENT_CONTROL_REQUEST_ID) ?: return
        val pending = AgentControlModel.pending.value ?: return
        if (pending.request.requestId != requestId) return
        val activeRoom = room ?: return
        scope.launch {
            runCatching {
                val profile = profiles.profile.first()
                require(profile.isEnrolled) { "device enrollment is unavailable" }
                val now = java.time.Instant.now()
                val confirmation = AgentControlConfirmation.newBuilder()
                    .setSchemaVersion(1)
                    .setConfirmationId(java.util.UUID.randomUUID().toString())
                    .setRequestId(requestId)
                    .setOperation(pending.request.operation)
                    .setOrigin(
                        AgentControlOrigin.newBuilder()
                            .setSource(SourceKind.SOURCE_KIND_USER)
                            .setActorId(profile.participantIdentity)
                            .setSourceMessageId(java.util.UUID.randomUUID().toString())
                            .build(),
                    )
                    .setApproved(approved)
                    .setIdempotencyKey(java.util.UUID.randomUUID().toString())
                    .setOccurredAt(
                        com.google.protobuf.Timestamp.newBuilder()
                            .setSeconds(now.epochSecond)
                            .setNanos(now.nano)
                            .build(),
                    )
                    .build()
                val event = ClientEvent.newBuilder()
                    .setProtocolVersion(2)
                    .setAgentControlConfirmation(confirmation)
                    .build()
                activeRoom.localParticipant.publishData(
                    event.toByteArray(),
                    DataPublishReliability.RELIABLE,
                )
            }.onSuccess {
                AgentControlModel.clear(requestId)
                notify(if (approved) "Applying conversation change" else "Conversation change canceled", false)
            }.onFailure { error ->
                SessionModel.message(
                    DisplayMessage(
                        "agent-control-confirmation-error-$requestId",
                        MessageKind.SYSTEM,
                        "Could not send confirmation: ${error.message ?: "unknown error"}",
                    ),
                )
            }
        }
    }

    private fun applySessionTransition(metadata: Map<String, String>) {
        if (metadata["radcliffe.session_transition.status"] != "applied") return
        val requestId = metadata["radcliffe.session_transition.request_id"] ?: return
        if (!handledTransitions.add(requestId)) return
        val action = metadata["radcliffe.session_transition.action"] ?: return
        val destination = metadata["radcliffe.session_transition.destination_session_id"].orEmpty()
        AgentControlModel.clear(requestId)
        scope.launch {
            if (action == "end") {
                runCatching { profiles.clearSessionSelection() }
                SessionModel.prepare()
                stopSession()
                return@launch
            }
            if (action !in setOf("create", "switch", "restart") || destination.isBlank()) {
                SessionModel.message(
                    DisplayMessage(
                        "session-transition-error-$requestId",
                        MessageKind.SYSTEM,
                        "Conversation transition was invalid",
                    ),
                )
                return@launch
            }
            SessionModel.state(VoiceState.RECONNECTING)
            notify("Switching conversation", false)
            runCatching {
                val profile = profiles.profile.first()
                require(profile.isEnrolled) { "device enrollment is unavailable" }
                profiles.selectSession(destination)
                val (timeline, credentials) = withContext(Dispatchers.IO) {
                    val history = if (action == "switch") {
                        SessionApi.timeline(
                            profile.controlUrl,
                            profile.deviceCredential,
                            destination,
                        )
                    } else {
                        emptyList()
                    }
                    history to SessionApi.reopen(
                        profile.controlUrl,
                        profile.deviceCredential,
                        destination,
                    )
                }
                replaceSession(credentials, timeline, destination)
            }.onFailure { error ->
                SessionModel.state(VoiceState.FAILED)
                SessionModel.message(
                    DisplayMessage(
                        "session-transition-error-$requestId",
                        MessageKind.SYSTEM,
                        "Conversation transition failed: ${error.message ?: "unknown error"}",
                    ),
                )
                stopSession(resetState = false)
            }
        }
    }

    private suspend fun replaceSession(
        credentials: SessionCredentials,
        timeline: List<DisplayMessage>,
        destination: String,
    ) {
        val previousRoom = room
        if (previousRoom != null) {
            playbackTracker.flush("conversation transition").forEach {
                runCatching { publishPlaybackAcknowledgement(previousRoom, it) }
            }
        }
        playbackSinks.toMap().forEach { (track, sink) -> track.removeSink(sink) }
        playbackSinks.clear()
        previousRoom?.disconnect()
        previousRoom?.release()
        room = null
        SessionModel.open(destination, timeline)
        SessionModel.state(VoiceState.CONNECTING)
        connectRoom(credentials.liveKitUrl, credentials.participantToken, destination)
        notify("Waiting for microphone", false)
    }

    private fun attachPlaybackSink(track: io.livekit.android.room.track.Track) {
        val audioTrack = track as? RemoteAudioTrack ?: return
        if (playbackSinks.containsKey(audioTrack)) return
        val sink = AudioTrackSink { audioData, bitsPerSample, sampleRate, _, numberOfFrames, _ ->
            val acknowledgements = playbackTracker.onAudioFrame(
                sampleRate = sampleRate,
                numberOfFrames = numberOfFrames,
                audible = DevicePlaybackTracker.isAudible(audioData, bitsPerSample),
            )
            val activeRoom = room
            if (activeRoom != null && acknowledgements.isNotEmpty()) {
                scope.launch {
                    acknowledgements.forEach {
                        publishPlaybackAcknowledgement(activeRoom, it)
                    }
                }
            }
        }
        playbackSinks[audioTrack] = sink
        audioTrack.addSink(sink)
    }

    private fun detachPlaybackSink(track: io.livekit.android.room.track.Track) {
        val audioTrack = track as? RemoteAudioTrack ?: return
        playbackSinks.remove(audioTrack)?.let(audioTrack::removeSink)
        val activeRoom = room ?: return
        val failed = playbackTracker.fail("remote audio track unsubscribed")
        if (failed.isNotEmpty()) {
            scope.launch {
                failed.forEach { publishPlaybackAcknowledgement(activeRoom, it) }
            }
        }
    }

    private suspend fun publishPlaybackAcknowledgement(
        activeRoom: Room,
        acknowledgement: PlaybackAcknowledgement,
    ) {
        val event = ClientEvent.newBuilder()
            .setProtocolVersion(2)
            .setPlaybackAcknowledgement(acknowledgement)
            .build()
        activeRoom.localParticipant.publishData(
            event.toByteArray(),
            DataPublishReliability.RELIABLE,
        )
    }

    private fun stopSession(resetState: Boolean = true) {
        room?.disconnect()
        room?.release()
        room = null
        playbackPaused = false
        playbackSuppressedForInterruption = false
        if (resetState) SessionModel.state(VoiceState.IDLE)
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
        playbackSinks.toMap().forEach { (track, sink) -> track.removeSink(sink) }
        playbackSinks.clear()
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
        const val ACTION_CONFIRM_AGENT_CONTROL = "works.ravd.radcliffe.CONFIRM_AGENT_CONTROL"
        const val ACTION_REJECT_AGENT_CONTROL = "works.ravd.radcliffe.REJECT_AGENT_CONTROL"
        const val EXTRA_URL = "livekit_url"
        const val EXTRA_TOKEN = "participant_token"
        const val EXTRA_SESSION_ID = "session_id"
        const val EXTRA_AGENT_CONTROL_REQUEST_ID = "agent_control_request_id"
        private const val CHANNEL_ID = "voice-session"
        private const val NOTIFICATION_ID = 1001
    }
}
