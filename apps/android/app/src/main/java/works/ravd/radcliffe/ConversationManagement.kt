package works.ravd.radcliffe

import java.time.Instant
import java.time.ZoneOffset

data class ConversationExport(
    val session: SessionSummary,
    val messages: List<DisplayMessage>,
    val includeSystemMessages: Boolean,
)

internal fun visibleConversationMessages(
    messages: List<DisplayMessage>,
    showSystemMessages: Boolean,
): List<DisplayMessage> = if (showSystemMessages) {
    messages
} else {
    messages.filterNot { it.kind == MessageKind.SYSTEM }
}

internal fun exportFileName(session: SessionSummary): String {
    val date = session.createdAt.atZone(ZoneOffset.UTC).toLocalDate()
    val safeSession = session.sessionId.replace(Regex("[^A-Za-z0-9_-]"), "-").take(96)
    return "radcliffe-$date-$safeSession.md"
}

internal fun renderConversationMarkdown(export: ConversationExport): String {
    val session = export.session
    val messages = visibleConversationMessages(export.messages, export.includeSystemMessages)
        .sortedWith(
            compareBy<DisplayMessage>(
                { it.sequence ?: Long.MAX_VALUE },
                { it.createdAt ?: Instant.MAX },
                { it.id },
            ),
        )
    return buildString {
        appendLine("# Radcliffe conversation")
        appendLine()
        session.title?.takeIf { it.isNotBlank() }?.let {
            appendLine("**Title:** ${safeMarkdownInline(it)}")
        }
        appendLine("**Session:** `${safeMarkdownInline(session.sessionId)}`")
        appendLine("**Started:** ${session.createdAt}")
        appendLine("**Status:** ${safeMarkdownInline(session.status)}")
        appendLine()
        if (messages.isEmpty()) {
            appendLine("_No durable messages._")
            return@buildString
        }
        messages.forEach { message ->
            val timestamp = message.createdAt?.toString()?.let { " — $it" }.orEmpty()
            appendLine("## ${messageLabel(message)}$timestamp")
            if (message.kind == MessageKind.SOURCE && !message.provenance.isNullOrBlank()) {
                val safeLabel = message.provenance
                    .substringAfterLast('/')
                    .substringAfterLast('\\')
                appendLine(
                    "**Source:** ${safeMarkdownInline(safeLabel)}",
                )
                appendLine()
            }
            val text = redactSensitiveText(message.text)
            val fence = "`".repeat(maxOf(3, longestBacktickRun(text) + 1))
            appendLine("${fence}text")
            appendLine(text)
            appendLine(fence)
            appendLine()
        }
    }
}

internal fun redactSensitiveText(text: String): String {
    if (INTERNAL_METADATA.containsMatchIn(text)) return "[redacted internal metadata]"
    var redacted = text
    redacted = DEVICE_CREDENTIAL.replace(redacted, "[redacted device credential]")
    redacted = JWT.replace(redacted, "[redacted signed token]")
    redacted = PROVIDER_KEY.replace(redacted, "[redacted provider key]")
    redacted = BEARER.replace(redacted, "Bearer [redacted]")
    redacted = SENSITIVE_ASSIGNMENT.replace(redacted) { match ->
        "${match.groupValues[1]}: [redacted]"
    }
    redacted = HOST_PATH.replace(redacted, "[redacted host path]")
    redacted = WINDOWS_HOST_PATH.replace(redacted, "[redacted host path]")
    return redacted
}

private fun messageLabel(message: DisplayMessage): String = when (message.kind) {
    MessageKind.USER -> "User"
    MessageKind.MODEL -> "Model"
    MessageKind.SOURCE -> "Authorized source"
    MessageKind.SYSTEM -> "System"
}

private fun safeMarkdownInline(value: String): String = escapeMarkdownInline(
    redactSensitiveText(value).replace(Regex("[\\r\\n]+"), " ").trim(),
)

private fun escapeMarkdownInline(value: String): String = buildString {
    value.forEach { character ->
        if (character in MARKDOWN_INLINE_CHARACTERS) append('\\')
        append(character)
    }
}

private fun longestBacktickRun(value: String): Int = BACKTICK_RUN
    .findAll(value)
    .maxOfOrNull { it.value.length }
    ?: 0

private val DEVICE_CREDENTIAL = Regex("rdc_dev_[A-Za-z0-9_-]{43,128}")
private val JWT = Regex("\\beyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b")
private val PROVIDER_KEY = Regex("\\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\\b")
private val BEARER = Regex("(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]{12,}")
private val SENSITIVE_ASSIGNMENT = Regex(
    "(?i)\\b(authorization|bootstrap[_ -]?token|internal[_ -]?token|api[_ -]?(?:key|secret)|" +
        "capability[_ -]?grant)\\s*[:=]\\s*(?:\"[^\"]*\"|'[^']*'|\\S+)",
)
private val HOST_PATH = Regex("/(?:Users|home|private|var|opt)/[^\\s\"']+")
private val WINDOWS_HOST_PATH = Regex("[A-Za-z]:\\\\[^\\s\"']+")
private val INTERNAL_METADATA = Regex(
    "(?i)\\b(capability[_ ]grant|grant_id|device_auth_audit|request_digest|raw[_ ]grant)\\b",
)
private val BACKTICK_RUN = Regex("`+")
private val MARKDOWN_INLINE_CHARACTERS = setOf(
    '\\',
    '`',
    '*',
    '_',
    '{',
    '}',
    '[',
    ']',
    '<',
    '>',
    '#',
    '+',
    '-',
    '!',
    '|',
)
