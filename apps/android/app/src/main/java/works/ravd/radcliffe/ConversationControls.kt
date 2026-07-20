package works.ravd.radcliffe

data class ConfigurationCycleControlModel(
    val key: String,
    val label: String,
    val displayValue: String,
    val nextValue: String?,
    val accessibilityLabel: String,
    val enabled: Boolean,
)

private val configurationControlOrder = listOf(
    "default_speech_rate",
    "response_detail",
)

internal fun effectiveConfigurations(
    registered: List<RegisteredConfiguration>,
    live: Map<String, ConfigurationUpdate>,
): List<RegisteredConfiguration> = registered.map { setting ->
    val update = live[setting.key]
    if (update == null || update.revision < setting.revision) {
        setting
    } else {
        setting.copy(
            value = update.value,
            effectiveScope = update.effectiveScope,
            revision = update.revision,
        )
    }
}

internal fun configurationCycleControls(
    values: List<RegisteredConfiguration>,
    mutationInFlight: Boolean,
): List<ConfigurationCycleControlModel> {
    val byKey = values.associateBy { it.key }
    return configurationControlOrder.mapNotNull { key ->
        byKey[key]?.let { configurationCycleControl(it, mutationInFlight) }
    }
}

private fun configurationCycleControl(
    setting: RegisteredConfiguration,
    mutationInFlight: Boolean,
): ConfigurationCycleControlModel {
    val index = setting.allowedValues.indexOf(setting.value)
    val next = if (index >= 0 && setting.allowedValues.size > 1) {
        setting.allowedValues[(index + 1) % setting.allowedValues.size]
    } else {
        null
    }
    val label = when (setting.key) {
        "default_speech_rate" -> "Pace"
        "response_detail" -> "Detail"
        else -> setting.key
    }
    val display = configurationDisplayValue(setting.key, setting.value)
    val spoken = configurationSpokenValue(setting.key, setting.value)
    val state = when {
        mutationInFlight -> "Update in progress. Current value $spoken"
        next == null -> "Current value $spoken. No registered alternative"
        else -> "Current value $spoken. Double tap to change to " +
            configurationSpokenValue(setting.key, next)
    }
    return ConfigurationCycleControlModel(
        key = setting.key,
        label = label,
        displayValue = display,
        nextValue = next,
        accessibilityLabel = "$label. $state",
        enabled = next != null && !mutationInFlight,
    )
}

internal fun configurationDisplayValue(key: String, value: String): String = when (key) {
    "response_detail" -> when (value) {
        "brief" -> "+"
        "standard" -> "++"
        "detailed" -> "+++"
        else -> value
    }
    else -> value
}

private fun configurationSpokenValue(key: String, value: String): String = when (key) {
    "response_detail" -> when (value) {
        "brief" -> "brief"
        "standard" -> "standard"
        "detailed" -> "detailed"
        else -> value
    }
    "default_speech_rate" -> "$value speech rate"
    else -> value
}
