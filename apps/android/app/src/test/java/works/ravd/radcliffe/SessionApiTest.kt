package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.json.JSONObject

class SessionApiTest {
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
                    "overflow": false
                }""".trimIndent(),
            ),
        )

        assertEquals("ses_1", page.items.single().sessionId)
        assertNull(page.items.single().title)
        assertNull(page.nextCursor)
        assertEquals(3.1, usage.percentage, 0.0)
        assertTrue(usage.exact)
        assertFalse(usage.overflow)
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
                            "provenance_label": "demo.md"
                        }
                    ],
                    "next_after": 2
                }""".trimIndent(),
            ),
        )

        assertEquals(listOf(MessageKind.USER, MessageKind.SOURCE), page.messages.map { it.kind })
        assertEquals("demo.md", page.messages.last().provenance)
        assertEquals(2L, page.nextAfter)
    }
}
