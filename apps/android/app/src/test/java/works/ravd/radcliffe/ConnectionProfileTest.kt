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
