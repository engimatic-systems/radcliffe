package works.ravd.radcliffe

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import java.util.Base64
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConnectionProfileTest {
    @Test
    fun validationNormalizesFieldsAndNeverPrintsTheCredential() {
        val profile = ConnectionProfile(
            controlUrl = " http://192.168.1.2:8000/ ",
            bootstrapToken = " bootstrap-secret ",
            sessionId = " radcliffe-demo ",
        )

        assertNull(profile.validationError())
        assertEquals(
            ConnectionProfile("http://192.168.1.2:8000", "bootstrap-secret", "radcliffe-demo"),
            profile.normalized(),
        )
        assertFalse(profile.toString().contains("bootstrap-secret"))
        assertTrue(ConnectionProfile("localhost", "token").validationError() != null)
        assertTrue(ConnectionProfile("http://localhost", "token", "bad session").validationError() != null)
        assertNull(ConnectionProfile("http://localhost", "token", "").validationError())
    }

    @Test
    fun encryptedProfileSurvivesRepositoryRecreationWithoutPlaintextAtRest() = runBlocking {
        val dataStore = MemoryDataStore()
        val cipher = TestCipher()
        val expected = ConnectionProfile("http://192.168.1.2:8000/", "bootstrap-secret", "radcliffe-demo")
        ConnectionProfileRepository(dataStore, cipher).save(expected)

        val recreated = ConnectionProfileRepository(dataStore, cipher).profile.first()

        assertEquals(expected.normalized(), recreated)
        assertTrue(recreated.isConfigured)
        assertFalse(dataStore.snapshot().asMap().values.any { it == "bootstrap-secret" })
        assertTrue(dataStore.snapshot().asMap().values.any { it.toString().startsWith("test-v1.") })
    }

    @Test
    fun unreadableCredentialRequiresSetupWithoutErasingNonSecretSettings() = runBlocking {
        val dataStore = MemoryDataStore()
        val cipher = TestCipher()
        val repository = ConnectionProfileRepository(dataStore, cipher)
        repository.save(ConnectionProfile("http://10.0.0.1:8000", "valid-token", "radcliffe-demo"))

        val profile = ConnectionProfileRepository(dataStore, UnreadableCipher()).profile.first()

        assertEquals("http://10.0.0.1:8000", profile.controlUrl)
        assertEquals("radcliffe-demo", profile.sessionId)
        assertEquals("", profile.bootstrapToken)
        assertFalse(profile.isConfigured)
    }

    @Test
    fun clearRemovesPreferencesAndKeystoreMaterial() = runBlocking {
        val dataStore = MemoryDataStore()
        val cipher = TestCipher()
        val repository = ConnectionProfileRepository(dataStore, cipher)
        repository.save(ConnectionProfile("http://localhost:8000", "token", "radcliffe-demo"))

        repository.clear()

        assertTrue(dataStore.snapshot().asMap().isEmpty())
        assertTrue(cipher.cleared)
        assertEquals(ConnectionProfile(), repository.profile.first())
    }

    @Test
    fun pendingCreationKeySurvivesRecreationAndClearsOnlyAfterSelection() = runBlocking {
        val dataStore = MemoryDataStore()
        val cipher = TestCipher()
        val first = ConnectionProfileRepository(dataStore, cipher)
        first.save(ConnectionProfile("http://localhost:8000", "token"))

        val pending = first.beginSessionCreation()
        val recreated = ConnectionProfileRepository(dataStore, cipher)

        assertEquals(pending, recreated.beginSessionCreation())
        recreated.selectSession("ses_generated")
        assertEquals("ses_generated", recreated.profile.first().sessionId)
        assertTrue(recreated.beginSessionCreation() != pending)
        recreated.clearSessionSelection()
        assertEquals("", recreated.profile.first().sessionId)
    }

    @Test
    fun systemMessageVisibilityDefaultsHiddenAndPersistsIndependently() = runBlocking {
        val dataStore = MemoryDataStore()
        val cipher = TestCipher()
        val repository = ConnectionProfileRepository(dataStore, cipher)

        assertFalse(repository.showSystemMessages.first())
        repository.setShowSystemMessages(true)
        repository.save(ConnectionProfile("http://localhost:8000", "token"))

        val recreated = ConnectionProfileRepository(dataStore, cipher)
        assertTrue(recreated.showSystemMessages.first())
        assertTrue(recreated.profile.first().isConfigured)
    }

    @Test
    fun enrollmentPreparationSurvivesLostResponseAndCompletionDeletesBootstrap() = runBlocking {
        val dataStore = MemoryDataStore()
        val cipher = TestCipher()
        val credential = "rdc_dev_" + "a".repeat(43)
        val first = ConnectionProfileRepository(
            dataStore,
            cipher,
            installationIdFactory = { "android-installation-1234" },
            deviceCredentialFactory = { credential },
        )
        first.save(ConnectionProfile("http://localhost:8000", "bootstrap-secret"))

        val prepared = first.prepareEnrollment()
        val retry = ConnectionProfileRepository(
            dataStore,
            cipher,
            installationIdFactory = { error("must reuse installation") },
            deviceCredentialFactory = { error("must reuse credential") },
        ).prepareEnrollment()

        assertEquals(prepared.installationId, retry.installationId)
        assertEquals(credential, retry.deviceCredential)
        assertEquals("bootstrap-secret", retry.bootstrapToken)
        assertFalse(dataStore.snapshot().asMap().values.any { it == credential })
        first.completeEnrollment(
            "dev_" + "b".repeat(32),
            "radcliffe-dev_" + "b".repeat(32),
        )
        val enrolled = first.profile.first()
        assertTrue(enrolled.isEnrolled)
        assertTrue(enrolled.isConfigured)
        assertEquals("", enrolled.bootstrapToken)
        assertEquals(credential, enrolled.deviceCredential)

        first.invalidateEnrollment()
        val invalidated = first.profile.first()
        assertFalse(invalidated.isConfigured)
        assertEquals("", invalidated.deviceCredential)
        assertEquals("", invalidated.installationId)
    }
}

private class MemoryDataStore(initial: Preferences = emptyPreferences()) : DataStore<Preferences> {
    private val state = MutableStateFlow(initial)
    override val data: Flow<Preferences> = state

    override suspend fun updateData(transform: suspend (t: Preferences) -> Preferences): Preferences {
        val updated = transform(state.value)
        state.value = updated
        return updated
    }

    fun snapshot(): Preferences = state.value
}

private class TestCipher : BootstrapTokenCipher {
    var cleared = false

    override fun encrypt(plaintext: String): String =
        "test-v1.${Base64.getEncoder().encodeToString(plaintext.toByteArray())}"

    override fun decrypt(ciphertext: String): String {
        require(ciphertext.startsWith("test-v1."))
        return Base64.getDecoder().decode(ciphertext.removePrefix("test-v1.")).toString(Charsets.UTF_8)
    }

    override fun clear() {
        cleared = true
    }
}

private class UnreadableCipher : BootstrapTokenCipher {
    override fun encrypt(plaintext: String): String = error("not used")
    override fun decrypt(ciphertext: String): String = error("invalidated key")
    override fun clear() = Unit
}
