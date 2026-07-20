package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class SessionHomeTest {
    @Test
    fun contextIndicatorCoversAllThresholdsWithoutColorOnlyMeaning() {
        assertIndicator(0.0, ContextSeverity.NORMAL, "0%")
        assertIndicator(74.9, ContextSeverity.NORMAL, "74%")
        assertIndicator(75.0, ContextSeverity.WARNING, "!75%")
        assertIndicator(90.0, ContextSeverity.NEAR_LIMIT, "!!90%")
        assertIndicator(100.0, ContextSeverity.OVER_LIMIT, "×100%")

        val overflow = contextIndicator(ContextUsageState.Available(usage(50.0, overflow = true)))
        assertEquals(ContextSeverity.OVER_LIMIT, overflow.severity)
        assertTrue(overflow.accessibilityLabel.contains("over limit"))
    }

    @Test
    fun estimatedUnknownAndUnavailableStatesAreAnnouncedHonestly() {
        val estimated = contextIndicator(ContextUsageState.Available(usage(20.0, exact = false)))
        val loading = contextIndicator(ContextUsageState.Loading)
        val unavailable = contextIndicator(ContextUsageState.Unavailable)

        assertEquals("~20%", estimated.centerLabel)
        assertTrue(estimated.accessibilityLabel.contains("estimated count"))
        assertEquals(ContextSeverity.UNKNOWN, loading.severity)
        assertFalse(loading.progress)
        assertEquals("Context usage unavailable", unavailable.accessibilityLabel)
        assertFalse(unavailable.progress)
    }

    @Test
    fun coldResumeCostIsQualifiedAndIncludesAuditableDetails() {
        val exact = resumeCostUiModel(
            cost(minimum = 0.000125, maximum = 0.002685),
        )
        val estimated = resumeCostUiModel(
            cost(
                minimum = 0.000125,
                maximum = 0.002685,
                tokenBasis = "estimated_projected_payload",
            ),
        )

        assertEquals("Cold estimate ≤$0.0027", exact.visibleLabel)
        assertEquals("Approx. cold estimate ≤$0.0027", estimated.visibleLabel)
        assertTrue(exact.detail.contains("claude-haiku-4-5-20251001"))
        assertTrue(exact.detail.contains("512 output tokens"))
        assertTrue(exact.detail.contains("Pricing effective 2026-05-27"))
        assertTrue(exact.detail.contains("system and tool overhead"))
        assertTrue(exact.accessibilityLabel.contains("Cold estimate"))
    }

    @Test
    fun localOverflowAndUnknownCostStatesNeverFabricateACharge() {
        val local = resumeCostUiModel(
            cost(
                minimum = 0.0,
                maximum = 0.0,
                modelId = "qwen3:8b",
                noApiCharge = true,
            ),
        )
        val overflow = resumeCostUiModel(
            cost(
                minimum = null,
                maximum = null,
                available = false,
                reason = "context_overflow",
            ),
        )
        val unknown = resumeCostUiModel(
            cost(
                minimum = null,
                maximum = null,
                available = false,
                reason = "unknown_model",
            ),
        )

        assertEquals("No API charge", local.visibleLabel)
        assertTrue(local.detail.contains("local"))
        assertEquals("Resume cost unavailable", overflow.visibleLabel)
        assertTrue(overflow.detail.contains("context limit"))
        assertTrue(unknown.detail.contains("no reviewed pricing entry"))
        assertFalse(unknown.detail.contains("$0"))
        assertTrue(unavailableResumeCostUiModel().detail.contains("context accounting"))
    }

    @Test
    fun homeReducerCoversLoadingEmptyOfflineAndExpiredAuth() {
        val existing = recent("first")
        val loading = sessionHomeLoading(SessionHomeUiState(items = listOf(existing)))
        assertTrue(loading.loading)
        assertEquals(listOf(existing), loading.items)

        val empty = sessionHomeLoaded(emptyList(), null)
        assertFalse(empty.loading)
        assertTrue(empty.items.isEmpty())

        val offline = sessionHomeFailure(loading, IllegalStateException("network down"))
        assertEquals(SessionHomeProblem.OFFLINE, offline.problem)
        assertEquals(listOf(existing), offline.items)
        assertFalse(offline.loading)

        val unauthorized = sessionHomeFailure(
            SessionHomeUiState(),
            ControlApiException(401, "expired"),
        )
        assertEquals(SessionHomeProblem.UNAUTHORIZED, unauthorized.problem)
    }

    @Test
    fun paginationRetainsServerOrderAndDeduplicatesRetries() {
        val first = recent("first")
        val second = recent("second")
        val third = recent("third")
        val current = sessionHomeLoading(
            SessionHomeUiState(items = listOf(first, second), nextCursor = "cursor-1"),
            more = true,
        )

        val appended = sessionHomePageAppended(
            current,
            listOf(second, third),
            "cursor-2",
        )

        assertEquals(listOf("first", "second", "third"), appended.items.map { it.summary.sessionId })
        assertEquals("cursor-2", appended.nextCursor)
        assertFalse(appended.loadingMore)
        assertEquals(null, appended.problem)
    }

    @Test
    fun successfulArchiveOrRestoreRemovesOnlyTheMutatedRow() {
        val first = recent("first")
        val second = recent("second")
        val current = SessionHomeUiState(
            items = listOf(first, second),
            nextCursor = "cursor",
            problem = SessionHomeProblem.OFFLINE,
        )

        val removed = sessionHomeSessionRemoved(current, "first")

        assertEquals(listOf("second"), removed.items.map { it.summary.sessionId })
        assertEquals("cursor", removed.nextCursor)
        assertEquals(null, removed.problem)
    }

    @Test
    fun failedPreflightKeepsItsErrorWhileEndedConversationRefreshesHome() {
        assertFalse(shouldRefreshSessionHome(VoiceState.CONNECTING, VoiceState.IDLE))
        assertFalse(shouldRefreshSessionHome(VoiceState.FAILED, VoiceState.IDLE))
        assertTrue(shouldRefreshSessionHome(VoiceState.LISTENING, VoiceState.IDLE))
        assertTrue(shouldRefreshSessionHome(VoiceState.PAUSED, VoiceState.IDLE))
    }

    private fun assertIndicator(
        percentage: Double,
        severity: ContextSeverity,
        centerLabel: String,
    ) {
        val indicator = contextIndicator(ContextUsageState.Available(usage(percentage)))
        assertEquals(severity, indicator.severity)
        assertEquals(centerLabel, indicator.centerLabel)
        assertTrue(indicator.accessibilityLabel.contains("percent used"))
    }

    private fun usage(
        percentage: Double,
        exact: Boolean = true,
        overflow: Boolean = false,
    ): ContextUsage = ContextUsage(
        modelId = "qwen3:8b",
        contextLimit = 40960,
        projectedInputTokens = 100,
        reservedOutputTokens = 8192,
        remainingTokens = 32668,
        percentage = percentage,
        projectionVersion = "context-v2",
        measurementBasis = "test",
        exact = exact,
        overflow = overflow,
        resumeCost = cost(
            minimum = 0.0,
            maximum = 0.0,
            modelId = "qwen3:8b",
            noApiCharge = true,
        ),
    )

    private fun cost(
        minimum: Double?,
        maximum: Double?,
        modelId: String = "claude-haiku-4-5-20251001",
        available: Boolean = true,
        noApiCharge: Boolean = false,
        tokenBasis: String = "exact_projected_payload",
        reason: String? = null,
    ): ResumeCostEstimate = ResumeCostEstimate(
        available = available,
        noApiCharge = noApiCharge,
        provider = if (noApiCharge) "ollama" else "anthropic",
        modelId = modelId,
        currency = "USD",
        minimumUsd = minimum,
        maximumUsd = maximum,
        projectedInputTokens = 100,
        representativeOutputTokens = 512,
        tokenBasis = tokenBasis,
        cacheAssumption = "cold",
        pricingBasis = "test-basis",
        pricingEffectiveDate = "2026-05-27",
        pricingExpiresOn = null,
        inputRatePerMillion = if (noApiCharge) 0.0 else 1.25,
        outputRatePerMillion = if (noApiCharge) 0.0 else 5.0,
        unavailableReason = reason,
    )

    private fun recent(id: String) = RecentSessionItem(
        summary = SessionSummary(
            sessionId = id,
            createdAt = Instant.parse("2026-07-14T00:00:00Z"),
            lastActivityAt = Instant.parse("2026-07-14T00:00:00Z"),
            title = null,
            status = "active",
            workerStatus = "stopped",
        ),
        contextUsage = ContextUsageState.Loading,
    )
}
