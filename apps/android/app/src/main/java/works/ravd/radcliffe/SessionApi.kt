package works.ravd.radcliffe

import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.time.Instant
import org.json.JSONObject

data class SessionCredentials(
    val liveKitUrl: String,
    val roomName: String,
    val participantToken: String,
)

data class DeviceEnrollment(
    val deviceId: String,
    val participantIdentity: String,
)

data class SessionSummary(
    val sessionId: String,
    val createdAt: Instant,
    val lastActivityAt: Instant,
    val title: String?,
    val status: String,
    val workerStatus: String,
)

data class SessionPage(
    val items: List<SessionSummary>,
    val nextCursor: String?,
)

data class ContextUsage(
    val modelId: String,
    val contextLimit: Int,
    val projectedInputTokens: Int,
    val reservedOutputTokens: Int,
    val remainingTokens: Int,
    val percentage: Double,
    val projectionVersion: String,
    val measurementBasis: String,
    val exact: Boolean,
    val overflow: Boolean,
)

data class TimelinePage(
    val messages: List<DisplayMessage>,
    val nextAfter: Long?,
)

class ControlApiException(
    val statusCode: Int,
    detail: String,
) : IllegalStateException("Control plane returned $statusCode: $detail")

object SessionApi {
    internal fun participantIdentity(model: String): String {
        val sanitized = model
            .replace(Regex("[^A-Za-z0-9_.:-]+"), "-")
            .trim('-', '_', '.', ':')
            .take(90)
            .ifBlank { "android" }
        return "pixel-$sanitized"
    }

    fun enroll(
        baseUrl: String,
        bootstrapToken: String,
        installationId: String,
        displayName: String,
        deviceCredential: String,
    ): DeviceEnrollment {
        val body = JSONObject()
            .put("installation_id", installationId)
            .put("display_name", displayName)
            .put("credential", deviceCredential)
        return parseEnrollment(
            request(baseUrl, bootstrapToken, "POST", "/v1/devices/enroll", body),
        )
    }

    fun testConnection(baseUrl: String, deviceCredential: String) {
        request(baseUrl, deviceCredential, "GET", "/v1/devices/me")
    }

    fun list(
        baseUrl: String,
        bootstrapToken: String,
        limit: Int = 20,
        cursor: String? = null,
        status: String? = null,
    ): SessionPage {
        require(limit in 1..100)
        require(status in setOf(null, "active", "ended", "archived"))
        val query = buildString {
            append("?limit=$limit")
            if (cursor != null) {
                append("&cursor=")
                append(URLEncoder.encode(cursor, Charsets.UTF_8.name()))
            }
            if (status != null) {
                append("&status=")
                append(URLEncoder.encode(status, Charsets.UTF_8.name()))
            }
        }
        return parseSessionPage(request(baseUrl, bootstrapToken, "GET", "/v1/sessions$query"))
    }

    fun get(
        baseUrl: String,
        deviceCredential: String,
        sessionId: String,
    ): SessionSummary = parseSessionSummary(
        request(
            baseUrl,
            deviceCredential,
            "GET",
            "/v1/sessions/${encodePath(sessionId)}",
        ),
    )

    fun updateStatus(
        baseUrl: String,
        deviceCredential: String,
        sessionId: String,
        status: String,
    ): SessionSummary {
        require(status in setOf("active", "ended", "archived"))
        return parseSessionSummary(
            request(
                baseUrl,
                deviceCredential,
                "PATCH",
                "/v1/sessions/${encodePath(sessionId)}",
                JSONObject().put("status", status),
            ),
        )
    }

    fun createGenerated(
        baseUrl: String,
        bootstrapToken: String,
        idempotencyKey: String,
    ): SessionCredentials {
        val body = JSONObject()
            .put("participant_identity", participantIdentity(android.os.Build.MODEL))
            .put("role", "client")
            .put("idempotency_key", idempotencyKey)
        return parseCredentials(request(baseUrl, bootstrapToken, "POST", "/v1/sessions", body))
    }

    fun reopen(
        baseUrl: String,
        bootstrapToken: String,
        sessionId: String,
    ): SessionCredentials {
        val body = JSONObject()
            .put("participant_identity", participantIdentity(android.os.Build.MODEL))
            .put("role", "client")
        return parseCredentials(
            request(
                baseUrl,
                bootstrapToken,
                "POST",
                "/v1/sessions/${encodePath(sessionId)}/credentials",
                body,
            ),
        )
    }

    fun contextUsage(
        baseUrl: String,
        bootstrapToken: String,
        sessionId: String,
    ): ContextUsage = parseContextUsage(
        request(
            baseUrl,
            bootstrapToken,
            "GET",
            "/v1/sessions/${encodePath(sessionId)}/context-usage",
        ),
    )

    fun timeline(
        baseUrl: String,
        bootstrapToken: String,
        sessionId: String,
    ): List<DisplayMessage> {
        val messages = mutableListOf<DisplayMessage>()
        var after: Long? = null
        do {
            val suffix = after?.let { "?limit=100&after=$it" } ?: "?limit=100"
            val page = parseTimelinePage(
                request(
                    baseUrl,
                    bootstrapToken,
                    "GET",
                    "/v1/sessions/${encodePath(sessionId)}/messages$suffix",
                ),
            )
            messages += page.messages
            after = page.nextAfter
        } while (after != null)
        return messages.distinctBy { it.id }
    }

    internal fun parseSessionPage(json: JSONObject): SessionPage {
        val values = json.getJSONArray("items")
        val items = buildList {
            for (index in 0 until values.length()) {
                val item = values.getJSONObject(index)
                add(parseSessionSummary(item))
            }
        }
        return SessionPage(
            items = items,
            nextCursor = if (json.isNull("next_cursor")) null else json.getString("next_cursor"),
        )
    }

    internal fun parseContextUsage(json: JSONObject): ContextUsage = ContextUsage(
        modelId = json.getString("model_id"),
        contextLimit = json.getInt("context_limit"),
        projectedInputTokens = json.getInt("projected_input_tokens"),
        reservedOutputTokens = json.getInt("reserved_output_tokens"),
        remainingTokens = json.getInt("remaining_tokens"),
        percentage = json.getDouble("percentage").coerceIn(0.0, 100.0),
        projectionVersion = json.getString("projection_version"),
        measurementBasis = json.getString("measurement_basis"),
        exact = json.getBoolean("exact"),
        overflow = json.getBoolean("overflow"),
    )

    internal fun parseSessionSummary(json: JSONObject): SessionSummary = SessionSummary(
        sessionId = json.getString("session_id"),
        createdAt = Instant.parse(json.getString("created_at")),
        lastActivityAt = Instant.parse(json.getString("last_activity_at")),
        title = if (json.isNull("title")) null else json.getString("title"),
        status = json.getString("status"),
        workerStatus = json.getString("worker_status"),
    )

    internal fun parseTimelinePage(json: JSONObject): TimelinePage {
        val values = json.getJSONArray("items")
        val messages = buildList {
            for (index in 0 until values.length()) {
                val item = values.getJSONObject(index)
                val text = if (item.isNull("text")) null else item.getString("text")
                if (text != null) {
                    val kind = when (item.getString("source")) {
                        "user" -> MessageKind.USER
                        "model" -> MessageKind.MODEL
                        "text_source" -> MessageKind.SOURCE
                        else -> MessageKind.SYSTEM
                    }
                    add(
                        DisplayMessage(
                            id = item.getString("message_id"),
                            kind = kind,
                            text = text,
                            provenance = if (item.isNull("provenance_label")) {
                                null
                            } else {
                                item.getString("provenance_label")
                            },
                            createdAt = Instant.parse(item.getString("created_at")),
                            sequence = item.getLong("sequence"),
                        ),
                    )
                }
            }
        }
        return TimelinePage(
            messages = messages,
            nextAfter = if (json.isNull("next_after")) null else json.getLong("next_after"),
        )
    }

    internal fun parseCredentials(json: JSONObject): SessionCredentials = SessionCredentials(
        liveKitUrl = json.getString("livekit_url"),
        roomName = json.getString("room_name"),
        participantToken = json.getString("participant_token"),
    )

    internal fun parseEnrollment(json: JSONObject): DeviceEnrollment = DeviceEnrollment(
        deviceId = json.getString("device_id"),
        participantIdentity = json.getString("participant_identity"),
    )

    private fun request(
        baseUrl: String,
        bearerCredential: String,
        method: String,
        path: String,
        body: JSONObject? = null,
    ): JSONObject {
        val connection = URL("${baseUrl.trimEnd('/')}$path").openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = method
            connection.connectTimeout = 5_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("Authorization", "Bearer $bearerCredential")
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val code = connection.responseCode
            if (code !in 200..299) {
                val detail = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                throw ControlApiException(code, detail.ifBlank { "request failed" })
            }
            JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
        } finally {
            connection.disconnect()
        }
    }

    private fun encodePath(value: String): String = URLEncoder.encode(value, Charsets.UTF_8.name())
        .replace("+", "%20")
}
