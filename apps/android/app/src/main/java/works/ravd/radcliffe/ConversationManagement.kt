package works.ravd.radcliffe

import java.time.Instant
import java.time.ZoneOffset

data class ConversationExport(
    val session: SessionSummary,
    val messages: List<DisplayMessage>,
    val includeSystemMessages: Boolean,
)

enum class ConversationExportFormat(val extension: String) {
    MARKDOWN("md"),
    HTML("html"),
}

internal fun visibleConversationMessages(
    messages: List<DisplayMessage>,
    showSystemMessages: Boolean,
): List<DisplayMessage> = if (showSystemMessages) {
    messages
} else {
    messages.filterNot { it.kind == MessageKind.SYSTEM }
}

internal fun exportFileName(
    session: SessionSummary,
    format: ConversationExportFormat = ConversationExportFormat.MARKDOWN,
): String {
    val date = session.createdAt.atZone(ZoneOffset.UTC).toLocalDate()
    val safeSession = session.sessionId.replace(Regex("[^A-Za-z0-9_-]"), "-").take(96)
    return "radcliffe-$date-$safeSession.${format.extension}"
}

internal fun renderConversationMarkdown(export: ConversationExport): String {
    val session = export.session
    val messages = orderedExportMessages(export)
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
                appendLine(
                    "**Source:** ${safeMarkdownInline(sourceDisplayLabel(message.provenance))}",
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

internal fun renderConversationHtml(export: ConversationExport): String {
    val session = export.session
    val messages = orderedExportMessages(export)
    val title = session.title?.takeIf { it.isNotBlank() } ?: "Radcliffe conversation"
    return buildString {
        appendLine("<!doctype html>")
        appendLine("<html lang=\"en\">")
        appendLine("<head>")
        appendLine("<meta charset=\"utf-8\">")
        appendLine("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
        appendLine(
            "<meta http-equiv=\"Content-Security-Policy\" " +
                "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">",
        )
        appendLine("<title>${safeHtmlInline(title)}</title>")
        appendLine("<style>")
        appendLine(HTML_STYLES)
        appendLine("</style>")
        appendLine("</head>")
        appendLine("<body><main>")
        appendLine("<header>")
        appendLine("<h1>${safeHtmlInline(title)}</h1>")
        appendLine("<dl>")
        appendLine("<div><dt>Session</dt><dd>${safeHtmlInline(session.sessionId)}</dd></div>")
        appendLine("<div><dt>Started</dt><dd>${escapeHtml(session.createdAt.toString())}</dd></div>")
        appendLine("<div><dt>Status</dt><dd>${safeHtmlInline(session.status)}</dd></div>")
        appendLine("</dl>")
        appendLine("</header>")
        if (messages.isEmpty()) {
            appendLine("<p class=\"empty\">No durable messages.</p>")
        } else {
            messages.forEach { message ->
                val label = messageLabel(message)
                appendLine(
                    "<article class=\"message ${message.kind.name.lowercase()}\" " +
                        "aria-label=\"${escapeHtml(label)} message\">",
                )
                append("<h2>${escapeHtml(label)}</h2>")
                message.createdAt?.let { append("<time datetime=\"$it\">$it</time>") }
                appendLine()
                if (message.kind == MessageKind.SOURCE && !message.provenance.isNullOrBlank()) {
                    appendLine(
                        "<p class=\"source-detail\"><strong>Source:</strong> " +
                            "${safeHtmlInline(sourceDisplayLabel(message.provenance))}</p>",
                    )
                }
                appendLine("<pre>${escapeHtml(redactSensitiveText(message.text))}</pre>")
                appendLine("</article>")
            }
        }
        appendLine("</main></body>")
        appendLine("</html>")
    }
}

internal fun renderConversationExport(
    format: ConversationExportFormat,
    export: ConversationExport,
): String = when (format) {
    ConversationExportFormat.MARKDOWN -> renderConversationMarkdown(export)
    ConversationExportFormat.HTML -> renderConversationHtml(export)
}

private fun orderedExportMessages(export: ConversationExport): List<DisplayMessage> =
    visibleConversationMessages(export.messages, export.includeSystemMessages)
        .sortedWith(
            compareBy<DisplayMessage>(
                { it.sequence ?: Long.MAX_VALUE },
                { it.createdAt ?: Instant.MAX },
                { it.id },
            ),
        )

private fun sourceDisplayLabel(provenance: String): String = provenance
    .substringAfterLast('/')
    .substringAfterLast('\\')

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

private fun safeHtmlInline(value: String): String = escapeHtml(
    redactSensitiveText(value).replace(Regex("[\\r\\n]+"), " ").trim(),
)

private fun escapeHtml(value: String): String = buildString {
    value.forEach { character ->
        append(
            when (character) {
                '&' -> "&amp;"
                '<' -> "&lt;"
                '>' -> "&gt;"
                '"' -> "&quot;"
                '\'' -> "&#39;"
                else -> character
            },
        )
    }
}

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

private val HTML_STYLES = """
    :root { color-scheme: light dark; --bg:#f8fafc; --text:#111827; --card:#ffffff;
      --muted:#334155; --border:#64748b; --user:#eff6ff; --model:#f0fdf4;
      --source:#fffbeb; --system:#fef2f2; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font:1rem/1.55 system-ui,sans-serif; }
    main { width:min(52rem,100%); margin:auto; padding:1.25rem; }
    header,.message,.empty { background:var(--card); border:1px solid var(--border);
      border-radius:.75rem; padding:1rem; margin:0 0 1rem; }
    h1,h2 { line-height:1.2; overflow-wrap:anywhere; }
    h1 { margin:.1rem 0 1rem; } h2 { display:inline; margin:0 1rem 0 0; font-size:1.1rem; }
    dl { margin:0; } dl div { display:grid; grid-template-columns:6rem 1fr; gap:.5rem; }
    dt { font-weight:700; } dd { margin:0; overflow-wrap:anywhere; }
    time,.source-detail { color:var(--muted); } .source-detail { margin:.5rem 0 0; }
    pre { margin:.75rem 0 0; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word;
      font:inherit; }
    .user { background:var(--user); } .model { background:var(--model); }
    .source { background:var(--source); } .system { background:var(--system); }
    @media (prefers-color-scheme:dark) {
      :root { --bg:#0f172a; --text:#f8fafc; --card:#1e293b; --muted:#cbd5e1;
        --border:#94a3b8; --user:#172554; --model:#052e16; --source:#451a03; --system:#450a0a; }
    }
    @media print { body { background:#fff; color:#000; } main { width:100%; } }
""".trimIndent()
