package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.json.JSONObject

class SessionApiTest {
    @Test
    fun configurationParserRetainsRegistryBoundsAndUndoState() {
        val values = SessionApi.parseConfiguration(
            JSONObject(
                """{
                    "items": [{
                        "key": "default_speech_rate",
                        "value": "1.25x",
                        "effective_scope": "session",
                        "revision": 2,
                        "default": "1.0x",
                        "allowed_values": ["0.75x", "1.0x", "1.25x", "1.5x"],
                        "allowed_scopes": ["device", "operator_default", "session", "turn"],
                        "persistent_scopes": ["device", "operator_default", "session"],
                        "confirmation_by_scope": {"device": "user"}
                    }]
                }""".trimIndent(),
            ),
        )
        val mutation = SessionApi.parseConfigurationMutation(
            JSONObject(
                """{
                    "mutation_id": "configuration-1",
                    "previous_value": "1.0x",
                    "applied_value": "1.25x",
                    "revision": 2,
                    "replayed": false,
                    "effective": {
                        "key": "default_speech_rate",
                        "value": "1.25x",
                        "effective_scope": "session",
                        "revision": 2,
                        "default": "1.0x",
                        "allowed_values": ["0.75x", "1.0x", "1.25x", "1.5x"],
                        "allowed_scopes": ["device", "operator_default", "session", "turn"],
                        "persistent_scopes": ["device", "operator_default", "session"],
                        "confirmation_by_scope": {"device": "user"}
                    }
                }""".trimIndent(),
            ),
        )

        assertEquals("1.25x", values.single().value)
        assertEquals(listOf("0.75x", "1.0x", "1.25x", "1.5x"), values.single().allowedValues)
        assertEquals("configuration-1", mutation.mutationId)
        assertEquals("1.0x", mutation.previousValue)
    }

    @Test
    fun enrollmentParserReturnsOnlyServerOwnedIdentity() {
        val enrollment = SessionApi.parseEnrollment(
            JSONObject(
                """{
                    "device_id": "dev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "participant_identity": "radcliffe-dev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "display_name": "Pixel",
                    "created_at": "2026-07-17T00:00:00Z",
                    "last_authenticated_at": null,
                    "revoked": false
                }""".trimIndent(),
            ),
        )
        assertEquals("dev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", enrollment.deviceId)
        assertEquals(
            "radcliffe-dev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            enrollment.participantIdentity,
        )
    }

    @Test
    fun pixelModelProducesAContractSafeParticipantIdentity() {
        assertEquals("pixel-Pixel-8-Pro", SessionApi.participantIdentity("Pixel 8 Pro"))
    }

    @Test
    fun participantIdentityHasAValidFallbackAndMaximumLength() {
        assertEquals("pixel-android", SessionApi.participantIdentity(" "))
        assertTrue(SessionApi.participantIdentity("x".repeat(200)).length <= 96)
    }

    @Test
    fun sessionAndContextResponsesAreParsedWithoutRawIdsBecomingTitles() {
        val page = SessionApi.parseSessionPage(
            JSONObject(
                """{
                    "items": [{
                        "session_id": "ses_1",
                        "created_at": "2026-07-14T10:00:00Z",
                        "last_activity_at": "2026-07-14T11:00:00Z",
                        "title": null,
                        "status": "active",
                        "worker_status": "running"
                    }],
                    "next_cursor": null
                }""".trimIndent(),
            ),
        )
        val usage = SessionApi.parseContextUsage(
            JSONObject(
                """{
                    "model_id": "qwen3:8b",
                    "context_limit": 40960,
                    "projected_input_tokens": 1000,
                    "reserved_output_tokens": 8192,
                    "remaining_tokens": 31768,
                    "percentage": 3.1,
                    "projection_version": "context-v2",
                    "measurement_basis": "ollama-openai-prompt_tokens-v1",
                    "exact": true,
                    "overflow": false,
                    "resume_cost": {
                        "available": true,
                        "no_api_charge": false,
                        "provider": "anthropic",
                        "model_id": "claude-haiku-4-5-20251001",
                        "currency": "USD",
                        "minimum_usd": 0.00125,
                        "maximum_usd": 0.00381,
                        "projected_input_tokens": 1000,
                        "representative_output_tokens": 512,
                        "token_basis": "exact_projected_payload",
                        "cache_assumption": "cold",
                        "pricing_basis": "anthropic/haiku/test",
                        "pricing_effective_date": "2026-05-27",
                        "pricing_expires_on": null,
                        "input_rate_per_million": 1.25,
                        "output_rate_per_million": 5.0,
                        "unavailable_reason": null
                    }
                }""".trimIndent(),
            ),
        )

        assertEquals("ses_1", page.items.single().sessionId)
        assertNull(page.items.single().title)
        assertNull(page.nextCursor)
        assertEquals(3.1, usage.percentage, 0.0)
        assertTrue(usage.exact)
        assertFalse(usage.overflow)
        assertEquals("claude-haiku-4-5-20251001", usage.resumeCost.modelId)
        assertEquals(0.00381, usage.resumeCost.maximumUsd!!, 0.0)
        assertEquals("2026-05-27", usage.resumeCost.pricingEffectiveDate)
    }

    @Test
    fun timelineParserMapsSourcesAndRetainsPagination() {
        val page = SessionApi.parseTimelinePage(
            JSONObject(
                """{
                    "items": [
                        {
                            "sequence": 1,
                            "message_id": "user-1",
                            "created_at": "2026-07-14T10:00:00Z",
                            "source": "user",
                            "text": "Hello",
                            "provenance_label": null
                        },
                        {
                            "sequence": 2,
                            "message_id": "source-1",
                            "created_at": "2026-07-14T10:01:00Z",
                            "source": "text_source",
                            "text": "Canonical",
                            "provenance_label": "demo.md",
                            "delivery_label": "Fast (1.25×)"
                        }
                    ],
                    "next_after": 2
                }""".trimIndent(),
            ),
        )

        assertEquals(listOf(MessageKind.USER, MessageKind.SOURCE), page.messages.map { it.kind })
        assertEquals("demo.md", page.messages.last().provenance)
        assertEquals("Fast (1.25×)", page.messages.last().deliveryLabel)
        assertEquals(1L, page.messages.first().sequence)
        assertEquals("2026-07-14T10:00:00Z", page.messages.first().createdAt.toString())
        assertEquals(2L, page.nextAfter)
    }

    @Test
    fun timelineParserMarksOnlyAllowlistedModelTechnicalDetail() {
        val page = SessionApi.parseTimelinePage(
            JSONObject(
                """{
                    "items": [{
                        "sequence": 1,
                        "message_id": "model-detail",
                        "created_at": "2026-07-22T10:00:00Z",
                        "source": "model",
                        "text": "Job job_0123456789abcdef wrote /srv/private/report.json",
                        "provenance_label": null,
                        "delivery_label": null,
                        "technical_detail_categories": ["identifier", "path", "host-secret"]
                    }],
                    "next_after": null
                }""".trimIndent(),
            ),
        )

        assertEquals(
            setOf("identifier", "path"),
            page.messages.single().technicalDetailCategories,
        )
        assertTrue(machineDetailUiModel(page.messages.single()) != null)
    }

    @Test
    fun statusUpdateParserPreservesTitleAndArchiveState() {
        val summary = SessionApi.parseSessionSummary(
            JSONObject(
                """{
                    "session_id": "ses_archived",
                    "created_at": "2026-07-14T10:00:00Z",
                    "last_activity_at": "2026-07-14T11:00:00Z",
                    "title": "Keep me",
                    "status": "archived",
                    "protocol_version": 2,
                    "generation": 3,
                    "worker_status": "stopped"
                }""".trimIndent(),
            ),
        )

        assertEquals("Keep me", summary.title)
        assertEquals("archived", summary.status)
        assertEquals("stopped", summary.workerStatus)
    }

    @Test
    fun credentialParserAcceptsTheGeneratedAndReopenResponseContract() {
        val credentials = SessionApi.parseCredentials(
            JSONObject(
                """{
                    "session_id": "ses_abc123",
                    "created_at": "2026-07-15T02:47:20Z",
                    "last_activity_at": "2026-07-15T02:47:21Z",
                    "title": null,
                    "status": "active",
                    "protocol_version": 2,
                    "generation": 1,
                    "worker_status": "running",
                    "livekit_url": "ws://192.168.18.71:7880",
                    "room_name": "ses_abc123",
                    "participant_token": "header.payload.signature",
                    "expires_in_seconds": 600
                }""".trimIndent(),
            ),
        )

        assertEquals("ws://192.168.18.71:7880", credentials.liveKitUrl)
        assertEquals("ses_abc123", credentials.roomName)
        assertEquals("header.payload.signature", credentials.participantToken)
    }

    @Test
    fun backgroundJobParsersRetainOnlyBoundedLifecycleMetadata() {
        val jobs = SessionApi.parseBackgroundJobs(
            JSONObject(
                """{
                    "items": [{
                        "job_id": "job-1",
                        "session_id": "session-1",
                        "generation": 2,
                        "status": "waiting",
                        "notification_policy": "next_safe_pause",
                        "priority": 1,
                        "budget_usd": 0.5,
                        "max_runtime_seconds": 3600,
                        "revision": 4,
                        "cancel_requested": false,
                        "attempt": 1,
                        "created_at": "2026-07-18T12:00:00Z",
                        "updated_at": "2026-07-18T12:03:00Z"
                    }]
                }""".trimIndent(),
            ),
        )
        val mutation = SessionApi.parseBackgroundJobMutation(
            JSONObject(
                """{
                    "job": {
                        "job_id": "job-1",
                        "session_id": "session-1",
                        "generation": 2,
                        "status": "cancelled",
                        "notification_policy": "next_safe_pause",
                        "priority": 1,
                        "budget_usd": null,
                        "max_runtime_seconds": 3600,
                        "revision": 5,
                        "cancel_requested": true,
                        "attempt": 1,
                        "created_at": "2026-07-18T12:00:00Z",
                        "updated_at": "2026-07-18T12:04:00Z"
                    },
                    "replayed": false
                }""".trimIndent(),
            ),
        )

        assertEquals(BackgroundJobStatus.WAITING, jobs.single().status)
        assertEquals(4L, jobs.single().revision)
        assertFalse(jobs.single().toString().contains("instruction", ignoreCase = true))
        assertEquals(BackgroundJobStatus.CANCELLED, mutation.job.status)
        assertFalse(mutation.replayed)
    }
}
