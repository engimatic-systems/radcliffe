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
    val resumeCost: ResumeCostEstimate,
)

data class ResumeCostEstimate(
    val available: Boolean,
    val noApiCharge: Boolean,
    val provider: String?,
    val modelId: String,
    val currency: String,
    val minimumUsd: Double?,
    val maximumUsd: Double?,
    val projectedInputTokens: Int,
    val representativeOutputTokens: Int,
    val tokenBasis: String,
    val cacheAssumption: String,
    val pricingBasis: String?,
    val pricingEffectiveDate: String?,
    val pricingExpiresOn: String?,
    val inputRatePerMillion: Double?,
    val outputRatePerMillion: Double?,
    val unavailableReason: String?,
)

data class TimelinePage(
    val messages: List<DisplayMessage>,
    val nextAfter: Long?,
)

data class RegisteredConfiguration(
    val key: String,
    val value: String,
    val effectiveScope: String,
    val revision: Int,
    val defaultValue: String,
    val allowedValues: List<String>,
)

data class ConfigurationMutationResult(
    val mutationId: String,
    val previousValue: String?,
    val appliedValue: String?,
    val revision: Int,
    val replayed: Boolean,
    val effective: RegisteredConfiguration,
)

data class BackgroundJobMutationResult(
    val job: BackgroundJobSummary,
    val replayed: Boolean,
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

    fun configuration(
        baseUrl: String,
        deviceCredential: String,
        sessionId: String,
    ): List<RegisteredConfiguration> {
        val query = "?session_id=${URLEncoder.encode(sessionId, Charsets.UTF_8.name())}"
        return parseConfiguration(
            request(baseUrl, deviceCredential, "GET", "/v1/configuration$query"),
        )
    }

    fun jobs(
        baseUrl: String,
        deviceCredential: String,
        sessionId: String,
    ): List<BackgroundJobSummary> = parseBackgroundJobs(
        request(
            baseUrl,
            deviceCredential,
            "GET",
            "/v1/sessions/${encodePath(sessionId)}/jobs?limit=100",
        ),
    )

    fun commandJob(
        baseUrl: String,
        deviceCredential: String,
        jobId: String,
        operation: String,
        idempotencyKey: String,
        instruction: String? = null,
        priority: Int? = null,
    ): BackgroundJobMutationResult {
        require(operation in setOf("follow_up", "set_priority", "cancel"))
        require((operation == "follow_up") == (instruction != null))
        require((operation == "set_priority") == (priority != null))
        require(instruction == null || instruction.isNotBlank() && instruction.length <= 4_000)
        require(priority == null || priority in 0..4)
        val body = JSONObject()
            .put("operation", operation)
            .put("idempotency_key", idempotencyKey)
        instruction?.let { body.put("instruction", it) }
        priority?.let { body.put("priority", it) }
        return parseBackgroundJobMutation(
            request(
                baseUrl,
                deviceCredential,
                "POST",
                "/v1/jobs/${encodePath(jobId)}/commands",
                body,
            ),
        )
    }

    fun mutateConfiguration(
        baseUrl: String,
        deviceCredential: String,
        sessionId: String,
        operation: String,
        key: String,
        value: String? = null,
        expectedRevision: Int? = null,
        undoMutationId: String? = null,
        idempotencyKey: String,
    ): ConfigurationMutationResult {
        require(operation in setOf("set", "reset", "undo"))
        require(key in setOf("response_detail", "default_speech_rate"))
        val body = JSONObject()
            .put("operation", operation)
            .put("key", key)
            .put("scope", "session")
            .put("session_id", sessionId)
            .put("idempotency_key", idempotencyKey)
        value?.let { body.put("value", it) }
        expectedRevision?.let { body.put("expected_revision", it) }
        undoMutationId?.let { body.put("undo_mutation_id", it) }
        return parseConfigurationMutation(
            request(
                baseUrl,
                deviceCredential,
                "POST",
                "/v1/configuration/mutations",
                body,
            ),
        )
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

    internal fun parseConfiguration(json: JSONObject): List<RegisteredConfiguration> {
        val values = json.getJSONArray("items")
        return buildList {
            for (index in 0 until values.length()) {
                add(parseRegisteredConfiguration(values.getJSONObject(index)))
            }
        }
    }

    internal fun parseBackgroundJobs(json: JSONObject): List<BackgroundJobSummary> {
        val values = json.getJSONArray("items")
        return buildList {
            for (index in 0 until values.length()) {
                add(parseBackgroundJob(values.getJSONObject(index)))
            }
        }
    }

    internal fun parseBackgroundJobMutation(json: JSONObject): BackgroundJobMutationResult =
        BackgroundJobMutationResult(
            job = parseBackgroundJob(json.getJSONObject("job")),
            replayed = json.getBoolean("replayed"),
        )

    internal fun parseBackgroundJob(json: JSONObject): BackgroundJobSummary =
        BackgroundJobSummary(
            jobId = json.getString("job_id"),
            sessionId = json.getString("session_id"),
            generation = json.getLong("generation"),
            status = BackgroundJobStatus.parse(json.getString("status")),
            notificationPolicy = json.getString("notification_policy"),
            priority = json.getInt("priority"),
            budgetUsd = if (json.isNull("budget_usd")) null else json.getDouble("budget_usd"),
            maxRuntimeSeconds = json.getInt("max_runtime_seconds"),
            revision = json.getLong("revision"),
            cancelRequested = json.getBoolean("cancel_requested"),
            attempt = json.getInt("attempt"),
            createdAt = Instant.parse(json.getString("created_at")),
            updatedAt = Instant.parse(json.getString("updated_at")),
        )

    internal fun parseConfigurationMutation(json: JSONObject): ConfigurationMutationResult =
        ConfigurationMutationResult(
            mutationId = json.getString("mutation_id"),
            previousValue = if (json.isNull("previous_value")) {
                null
            } else {
                json.getString("previous_value")
            },
            appliedValue = if (json.isNull("applied_value")) {
                null
            } else {
                json.getString("applied_value")
            },
            revision = json.getInt("revision"),
            replayed = json.getBoolean("replayed"),
            effective = parseRegisteredConfiguration(json.getJSONObject("effective")),
        )

    private fun parseRegisteredConfiguration(json: JSONObject): RegisteredConfiguration {
        val allowed = json.getJSONArray("allowed_values")
        return RegisteredConfiguration(
            key = json.getString("key"),
            value = json.getString("value"),
            effectiveScope = json.getString("effective_scope"),
            revision = json.getInt("revision"),
            defaultValue = json.getString("default"),
            allowedValues = buildList {
                for (index in 0 until allowed.length()) add(allowed.getString(index))
            },
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
        resumeCost = parseResumeCost(json.getJSONObject("resume_cost")),
    )

    private fun parseResumeCost(json: JSONObject): ResumeCostEstimate = ResumeCostEstimate(
        available = json.getBoolean("available"),
        noApiCharge = json.getBoolean("no_api_charge"),
        provider = if (json.isNull("provider")) null else json.getString("provider"),
        modelId = json.getString("model_id"),
        currency = json.getString("currency"),
        minimumUsd = if (json.isNull("minimum_usd")) null else json.getDouble("minimum_usd"),
        maximumUsd = if (json.isNull("maximum_usd")) null else json.getDouble("maximum_usd"),
        projectedInputTokens = json.getInt("projected_input_tokens"),
        representativeOutputTokens = json.getInt("representative_output_tokens"),
        tokenBasis = json.getString("token_basis"),
        cacheAssumption = json.getString("cache_assumption"),
        pricingBasis = if (json.isNull("pricing_basis")) null else json.getString("pricing_basis"),
        pricingEffectiveDate = if (json.isNull("pricing_effective_date")) {
            null
        } else {
            json.getString("pricing_effective_date")
        },
        pricingExpiresOn = if (json.isNull("pricing_expires_on")) {
            null
        } else {
            json.getString("pricing_expires_on")
        },
        inputRatePerMillion = if (json.isNull("input_rate_per_million")) {
            null
        } else {
            json.getDouble("input_rate_per_million")
        },
        outputRatePerMillion = if (json.isNull("output_rate_per_million")) {
            null
        } else {
            json.getDouble("output_rate_per_million")
        },
        unavailableReason = if (json.isNull("unavailable_reason")) {
            null
        } else {
            json.getString("unavailable_reason")
        },
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
                            deliveryLabel = if (item.isNull("delivery_label")) {
                                null
                            } else {
                                item.getString("delivery_label")
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
