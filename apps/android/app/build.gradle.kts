import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

val reviewKeystorePath = providers.environmentVariable("RADCLIFFE_ANDROID_REVIEW_KEYSTORE").orNull
val reviewStorePassword =
    providers.environmentVariable("RADCLIFFE_ANDROID_REVIEW_STORE_PASSWORD").orNull
val reviewKeyAlias = providers.environmentVariable("RADCLIFFE_ANDROID_REVIEW_KEY_ALIAS").orNull
val reviewKeyPassword = providers.environmentVariable("RADCLIFFE_ANDROID_REVIEW_KEY_PASSWORD").orNull
val reviewSigningConfigured = listOf(
    reviewKeystorePath,
    reviewStorePassword,
    reviewKeyAlias,
    reviewKeyPassword,
).all { !it.isNullOrBlank() }

android {
    namespace = "works.ravd.radcliffe"
    compileSdk = 36

    defaultConfig {
        applicationId = "works.ravd.radcliffe"
        minSdk = 29
        targetSdk = 36
        versionCode = 13
        versionName = "0.6.2"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (reviewSigningConfigured) {
            create("review") {
                storeFile = file(requireNotNull(reviewKeystorePath))
                storePassword = requireNotNull(reviewStorePassword)
                keyAlias = requireNotNull(reviewKeyAlias)
                keyPassword = requireNotNull(reviewKeyPassword)
            }
        }
    }

    buildTypes {
        getByName("debug") {
            buildConfigField("boolean", "ALLOW_CLEARTEXT_CONTROL", "true")
        }
        getByName("release") {
            buildConfigField("boolean", "ALLOW_CLEARTEXT_CONTROL", "false")
        }
        create("review") {
            initWith(getByName("release"))
            buildConfigField("boolean", "ALLOW_CLEARTEXT_CONTROL", "false")
            matchingFallbacks += listOf("release")
            if (reviewSigningConfigured) {
                signingConfig = signingConfigs.getByName("review")
            }
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.lifecycle.runtime)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.media3.session)
    implementation(libs.androidx.media3.exoplayer)
    implementation(libs.livekit.android)
    implementation(libs.protobuf.kotlin.lite)

    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
    testImplementation(libs.json)
}
