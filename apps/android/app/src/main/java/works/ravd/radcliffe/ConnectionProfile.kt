package works.ravd.radcliffe

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.core.DataStore
import androidx.datastore.core.handlers.ReplaceFileCorruptionHandler
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.io.IOException
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map

data class ConnectionProfile(
    val controlUrl: String = "",
    val bootstrapToken: String = "",
    val sessionId: String = DEFAULT_SESSION_ID,
) {
    val isConfigured: Boolean
        get() = validationError() == null

    fun normalized(): ConnectionProfile = copy(
        controlUrl = controlUrl.trim().trimEnd('/'),
        bootstrapToken = bootstrapToken.trim(),
        sessionId = sessionId.trim(),
    )

    fun validationError(): String? {
        val value = normalized()
        if (!value.controlUrl.startsWith("http://") && !value.controlUrl.startsWith("https://")) {
            return "Control URL must start with http:// or https://"
        }
        if (value.bootstrapToken.isBlank()) return "Bootstrap token is required"
        if (value.sessionId.isNotBlank() && !SESSION_ID.matches(value.sessionId)) {
            return "Session must use 1-96 letters, numbers, underscores, or hyphens"
        }
        return null
    }

    override fun toString(): String =
        "ConnectionProfile(controlUrl=$controlUrl, bootstrapToken=[redacted], sessionId=$sessionId)"

    companion object {
        const val DEFAULT_SESSION_ID = ""
        private val SESSION_ID = Regex("^[A-Za-z0-9_-]{1,96}$")
    }
}

interface BootstrapTokenCipher {
    fun encrypt(plaintext: String): String
    fun decrypt(ciphertext: String): String
    fun clear()
}

class AndroidKeystoreBootstrapTokenCipher : BootstrapTokenCipher {
    private val keyStore: KeyStore
        get() = KeyStore.getInstance(KEYSTORE).apply { load(null) }

    override fun encrypt(plaintext: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP or Base64.URL_SAFE)
        val payload = Base64.encodeToString(
            cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8)),
            Base64.NO_WRAP or Base64.URL_SAFE,
        )
        return "v1.$iv.$payload"
    }

    override fun decrypt(ciphertext: String): String {
        val parts = ciphertext.split('.')
        require(parts.size == 3 && parts[0] == "v1") { "unsupported credential envelope" }
        val iv = Base64.decode(parts[1], Base64.NO_WRAP or Base64.URL_SAFE)
        val payload = Base64.decode(parts[2], Base64.NO_WRAP or Base64.URL_SAFE)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv))
        return cipher.doFinal(payload).toString(Charsets.UTF_8)
    }

    override fun clear() {
        keyStore.deleteEntry(KEY_ALIAS)
    }

    private fun secretKey(): SecretKey {
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        private const val KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "radcliffe.bootstrap-token.v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}

private val Context.connectionProfileDataStore by preferencesDataStore(
    name = "connection-profile",
    corruptionHandler = ReplaceFileCorruptionHandler { emptyPreferences() },
)

class ConnectionProfileRepository(
    private val dataStore: DataStore<Preferences>,
    private val tokenCipher: BootstrapTokenCipher,
) {
    val profile: Flow<ConnectionProfile> = dataStore.data
        .catch { exception ->
            if (exception is IOException) emit(emptyPreferences()) else throw exception
        }
        .map { preferences ->
            val token = preferences[BOOTSTRAP_TOKEN]
                ?.let { encrypted -> runCatching { tokenCipher.decrypt(encrypted) }.getOrNull() }
                .orEmpty()
            ConnectionProfile(
                controlUrl = preferences[CONTROL_URL].orEmpty(),
                bootstrapToken = token,
                sessionId = preferences[SESSION_ID] ?: ConnectionProfile.DEFAULT_SESSION_ID,
            )
        }

    suspend fun save(profile: ConnectionProfile) {
        val normalized = profile.normalized()
        normalized.validationError()?.let { error(it) }
        val encryptedToken = tokenCipher.encrypt(normalized.bootstrapToken)
        dataStore.edit { preferences ->
            preferences[CONTROL_URL] = normalized.controlUrl
            preferences[SESSION_ID] = normalized.sessionId
            preferences[BOOTSTRAP_TOKEN] = encryptedToken
        }
    }

    suspend fun clear() {
        dataStore.edit { it.clear() }
        tokenCipher.clear()
    }

    suspend fun beginSessionCreation(): String {
        var key = ""
        dataStore.edit { preferences ->
            key = preferences[PENDING_SESSION_KEY] ?: UUID.randomUUID().toString().also {
                preferences[PENDING_SESSION_KEY] = it
            }
        }
        return key
    }

    suspend fun selectSession(sessionId: String) {
        require(SESSION_ID_PATTERN.matches(sessionId)) { "invalid session ID" }
        dataStore.edit { preferences ->
            preferences[SESSION_ID] = sessionId
            preferences.remove(PENDING_SESSION_KEY)
        }
    }

    companion object {
        private val CONTROL_URL = stringPreferencesKey("control_url")
        private val SESSION_ID = stringPreferencesKey("session_id")
        private val BOOTSTRAP_TOKEN = stringPreferencesKey("bootstrap_token_encrypted")
        private val PENDING_SESSION_KEY = stringPreferencesKey("pending_session_creation_key")
        private val SESSION_ID_PATTERN = Regex("^[A-Za-z0-9_-]{1,96}$")

        fun create(context: Context): ConnectionProfileRepository = ConnectionProfileRepository(
            context.applicationContext.connectionProfileDataStore,
            AndroidKeystoreBootstrapTokenCipher(),
        )
    }
}
