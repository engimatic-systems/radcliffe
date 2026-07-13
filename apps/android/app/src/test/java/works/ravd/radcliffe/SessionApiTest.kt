package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

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
}
