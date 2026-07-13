package works.ravd.radcliffe

import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

data class SessionCredentials(
    val liveKitUrl: String,
    val roomName: String,
    val participantToken: String,
)

object SessionApi {
    internal fun participantIdentity(model: String): String {
        val sanitized = model
            .replace(Regex("[^A-Za-z0-9_.:-]+"), "-")
            .trim('-', '_', '.', ':')
            .take(90)
            .ifBlank { "android" }
        return "pixel-$sanitized"
    }

    fun create(baseUrl: String, bootstrapToken: String, sessionId: String): SessionCredentials {
        val connection = URL("${baseUrl.trimEnd('/')}/v1/sessions").openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.connectTimeout = 5_000
        connection.readTimeout = 10_000
        connection.doOutput = true
        connection.setRequestProperty("Authorization", "Bearer $bootstrapToken")
        connection.setRequestProperty("Content-Type", "application/json")
        val request = JSONObject()
            .put("session_id", sessionId)
            .put("participant_identity", participantIdentity(android.os.Build.MODEL))
            .put("role", "client")
        connection.outputStream.use { it.write(request.toString().toByteArray()) }
        val code = connection.responseCode
        if (code !in 200..299) {
            val detail = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
            connection.disconnect()
            error("Control plane returned $code: $detail")
        }
        val json = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
        connection.disconnect()
        return SessionCredentials(
            liveKitUrl = json.getString("livekit_url"),
            roomName = json.getString("room_name"),
            participantToken = json.getString("participant_token"),
        )
    }
}
