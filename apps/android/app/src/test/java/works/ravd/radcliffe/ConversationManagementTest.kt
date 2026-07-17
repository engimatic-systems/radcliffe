package works.ravd.radcliffe

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationManagementTest {
    @Test
    fun systemMessagesAreHiddenByDefaultWithoutChangingStoredOrder() {
        val messages = listOf(
            message("user", MessageKind.USER, 1),
            message("system", MessageKind.SYSTEM, 2),
            message("model", MessageKind.MODEL, 3),
        )

        val hidden = visibleConversationMessages(messages, showSystemMessages = false)
        val shown = visibleConversationMessages(messages, showSystemMessages = true)

        assertEquals(listOf("user", "model"), hidden.map { it.id })
        assertEquals(messages, shown)
        assertEquals(3, messages.size)
    }

    @Test
    fun markdownExportIsChronologicalPortableAndRedacted() {
        val credential = "rdc_dev_" + "a".repeat(43)
        val export = ConversationExport(
            session = session(title = "Notes\n# injected"),
            messages = listOf(
                message("model", MessageKind.MODEL, 3, "Unicode stays: café 日本語"),
                message(
                    "user",
                    MessageKind.USER,
                    1,
                    "Read ```this``` and keep <script>alert(1)</script> but not $credential",
                ),
                message(
                    "source",
                    MessageKind.SOURCE,
                    2,
                    "Canonical source text",
                    provenance = "/Users/private-owner/Documents/demo_[x].md",
                ),
                message("system", MessageKind.SYSTEM, 4, "authorization: secret-value"),
            ),
            includeSystemMessages = false,
        )

        val markdown = renderConversationMarkdown(export)

        assertTrue(markdown.startsWith("# Radcliffe conversation\n"))
        assertTrue(markdown.indexOf("## User") < markdown.indexOf("## Authorized source"))
        assertTrue(markdown.indexOf("## Authorized source") < markdown.indexOf("## Model"))
        assertFalse(markdown.contains("## System"))
        assertFalse(markdown.contains(credential))
        assertTrue(markdown.contains("[redacted device credential]"))
        assertFalse(markdown.contains("/Users/private-owner"))
        assertTrue(markdown.contains("demo\\_\\[x\\].md"))
        assertTrue(markdown.contains("````text\n"))
        assertTrue(markdown.contains("<script>alert(1)</script>"))
        assertTrue(markdown.contains("Unicode stays: café 日本語"))
        assertFalse(markdown.contains("\n# injected\n"))
    }

    @Test
    fun exportCanExplicitlyIncludeRedactedSystemMessages() {
        val export = ConversationExport(
            session = session(),
            messages = listOf(
                message(
                    "system",
                    MessageKind.SYSTEM,
                    1,
                    "Bearer abcdefghijklmnopqrstuvwxyz and /private/var/radcliffe/secret.json",
                ),
            ),
            includeSystemMessages = true,
        )

        val markdown = renderConversationMarkdown(export)

        assertTrue(markdown.contains("## System"))
        assertTrue(markdown.contains("Bearer [redacted]"))
        assertTrue(markdown.contains("[redacted host path]"))
        assertFalse(markdown.contains("abcdefghijklmnopqrstuvwxyz"))
        assertFalse(markdown.contains("/private/var"))
    }

    @Test
    fun rawGrantAndAuditMetadataAreExcludedAsAUnit() {
        val text = "capability_grant: { grant_id: source:secret, request_digest: abc123 }"

        assertEquals("[redacted internal metadata]", redactSensitiveText(text))
    }

    @Test
    fun emptyExportAndFilenameAreDeterministic() {
        val session = session()

        assertEquals(
            "radcliffe-2026-07-14-ses_export.md",
            exportFileName(session),
        )
        assertTrue(
            renderConversationMarkdown(
                ConversationExport(session, emptyList(), includeSystemMessages = false),
            ).contains("_No durable messages._"),
        )
    }

    private fun session(title: String? = null) = SessionSummary(
        sessionId = "ses_export",
        createdAt = Instant.parse("2026-07-14T10:00:00Z"),
        lastActivityAt = Instant.parse("2026-07-14T11:00:00Z"),
        title = title,
        status = "active",
        workerStatus = "running",
    )

    private fun message(
        id: String,
        kind: MessageKind,
        sequence: Long,
        text: String = id,
        provenance: String? = null,
    ) = DisplayMessage(
        id = id,
        kind = kind,
        text = text,
        provenance = provenance,
        createdAt = Instant.parse("2026-07-14T10:00:0${sequence}Z"),
        sequence = sequence,
    )
}
