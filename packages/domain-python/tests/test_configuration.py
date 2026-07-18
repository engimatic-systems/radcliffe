from __future__ import annotations

import asyncio
from dataclasses import replace

import aiosqlite
import pytest
from radcliffe_domain.configuration import (
    SETTING_DEFINITIONS,
    ConfigurationConfirmationRequired,
    ConfigurationConflictError,
    ConfigurationContext,
    ConfigurationCorruptionError,
    ConfigurationError,
    ConfigurationKey,
    ConfigurationMutation,
    ConfigurationRegistry,
    ConfigurationScope,
    normalize_configuration_value,
)
from radcliffe_domain.store import EventStore


async def registry(tmp_path) -> ConfigurationRegistry:
    path = tmp_path / "events.sqlite3"
    await EventStore(path).migrate()
    return ConfigurationRegistry(path)


def mutation(
    context: ConfigurationContext,
    *,
    mutation_id: str,
    key: str = "default_speech_rate",
    scope: str = "session",
    kind: str = "set",
    value: str | None = "fast",
    expected_revision: int | None = None,
    undo_mutation_id: str | None = None,
    confirmed: bool = False,
) -> ConfigurationMutation:
    return ConfigurationMutation(
        mutation_id=mutation_id,
        idempotency_key=f"idempotency:{mutation_id}",
        key=key,
        scope=scope,
        kind=kind,
        context=context,
        actor_id="agent-1",
        value=value,
        expected_revision=expected_revision,
        undo_mutation_id=undo_mutation_id,
        confirmed=confirmed,
    )


def test_registry_definitions_are_closed_bounded_and_document_persistence() -> None:
    assert set(SETTING_DEFINITIONS) == {
        ConfigurationKey.RESPONSE_DETAIL,
        ConfigurationKey.DEFAULT_SPEECH_RATE,
    }
    pace = SETTING_DEFINITIONS[ConfigurationKey.DEFAULT_SPEECH_RATE]
    assert pace.default == "1.0x"
    assert pace.allowed_values == ("0.75x", "1.0x", "1.25x", "1.5x")
    assert ConfigurationScope.TURN not in pace.persistent_scopes
    assert pace.confirmation_by_scope[ConfigurationScope.DEVICE] == "user"
    assert pace.confirmation_by_scope[ConfigurationScope.OPERATOR_DEFAULT] == "operator"


@pytest.mark.parametrize(
    ("key", "spoken", "canonical"),
    [
        ("response_detail", "concise", "brief"),
        ("response_detail", "normal", "standard"),
        ("response_detail", "thorough", "detailed"),
        ("default_speech_rate", "slowly", "0.75x"),
        ("default_speech_rate", "regular", "1.0x"),
        ("default_speech_rate", "quickly", "1.25x"),
        ("default_speech_rate", "very-fast", "1.5x"),
    ],
)
def test_registered_synonyms_normalize_to_typed_values(
    key: str,
    spoken: str,
    canonical: str,
) -> None:
    assert normalize_configuration_value(key, spoken) == canonical


@pytest.mark.parametrize(
    "key",
    ["api_key", "endpoint", "capability_grant", "developer_options", "paid_provider_policy"],
)
def test_sensitive_or_arbitrary_settings_are_not_registered(key: str) -> None:
    with pytest.raises(ConfigurationError, match="not registered"):
        normalize_configuration_value(key, "anything")


@pytest.mark.asyncio
async def test_effective_precedence_and_turn_expiry_are_exact(tmp_path) -> None:
    selected = await registry(tmp_path)
    context = ConfigurationContext(
        owner_id="owner",
        device_id="device-1",
        session_id="session-1",
        turn_id="turn-1",
    )
    await selected.mutate(
        mutation(
            context,
            mutation_id="operator",
            scope="operator_default",
            value="slow",
            confirmed=True,
        )
    )
    await selected.mutate(
        mutation(
            context,
            mutation_id="device",
            scope="device",
            value="normal",
            confirmed=True,
        )
    )
    await selected.mutate(mutation(context, mutation_id="session", value="fast"))
    turn = await selected.mutate(
        mutation(context, mutation_id="turn", scope="turn", value="very fast")
    )

    assert turn.effective.value == "1.5x"
    assert turn.effective.effective_scope == "turn"
    assert [layer.scope for layer in turn.effective.layers] == [
        "operator_default",
        "device",
        "session",
        "turn",
    ]
    next_turn = await selected.inspect(
        "default_speech_rate",
        ConfigurationContext(
            owner_id="owner",
            device_id="device-1",
            session_id="session-1",
            turn_id="turn-2",
        ),
    )
    assert next_turn.value == "1.25x"
    assert next_turn.effective_scope == "session"


@pytest.mark.asyncio
async def test_persistent_values_survive_registry_recreation_and_reset(tmp_path) -> None:
    selected = await registry(tmp_path)
    context = ConfigurationContext(owner_id="owner", session_id="session-1")
    applied = await selected.mutate(
        mutation(
            context,
            mutation_id="detail",
            key="response_detail",
            value="thorough",
        )
    )

    reconnected = ConfigurationRegistry(selected.path)
    effective = await reconnected.inspect("response_detail", context)
    reset = await reconnected.mutate(
        mutation(
            context,
            mutation_id="reset",
            key="response_detail",
            kind="reset",
            value=None,
            expected_revision=applied.revision,
        )
    )

    assert effective.value == "detailed"
    assert reset.applied_value is None
    assert reset.effective.value == "standard"
    assert reset.effective.effective_scope == "built_in"


@pytest.mark.asyncio
async def test_replay_conflict_concurrent_revision_and_undo_are_deterministic(tmp_path) -> None:
    selected = await registry(tmp_path)
    context = ConfigurationContext(owner_id="owner", session_id="session-1")
    request = mutation(context, mutation_id="first", value="fast")
    first = await selected.mutate(request)
    replay = await selected.mutate(request)

    assert replay.replayed
    assert replay.revision == first.revision
    with pytest.raises(ConfigurationConflictError, match="idempotency"):
        await selected.mutate(replace(request, value="slow"))
    with pytest.raises(ConfigurationConflictError, match="concurrently"):
        await selected.mutate(
            mutation(
                context,
                mutation_id="stale-device",
                value="slow",
                expected_revision=0,
            )
        )
    undo = await selected.mutate(
        mutation(
            context,
            mutation_id="undo",
            kind="undo",
            value=None,
            undo_mutation_id="first",
            expected_revision=first.revision,
        )
    )
    assert undo.applied_value is None
    assert undo.effective.value == "1.0x"


@pytest.mark.asyncio
async def test_confirmation_and_corruption_fail_visibly_without_silent_change(tmp_path) -> None:
    selected = await registry(tmp_path)
    context = ConfigurationContext(owner_id="owner", device_id="device-1")
    with pytest.raises(ConfigurationConfirmationRequired, match="user confirmation"):
        await selected.mutate(mutation(context, mutation_id="device", scope="device", value="fast"))

    await selected.mutate(
        mutation(
            context,
            mutation_id="confirmed-device",
            scope="device",
            value="fast",
            confirmed=True,
        )
    )
    async with aiosqlite.connect(selected.path) as database:
        await database.execute(
            "UPDATE configuration_values SET value = 'credential:secret' "
            "WHERE setting_key = 'default_speech_rate'"
        )
        await database.commit()

    with pytest.raises(ConfigurationCorruptionError, match="corrupt"):
        await selected.inspect("default_speech_rate", context)


@pytest.mark.asyncio
async def test_concurrent_device_writes_with_one_revision_have_one_winner(tmp_path) -> None:
    selected = await registry(tmp_path)
    context = ConfigurationContext(owner_id="owner", device_id="device-1")

    async def apply(mutation_id: str, value: str):
        return await selected.mutate(
            mutation(
                context,
                mutation_id=mutation_id,
                scope="device",
                value=value,
                expected_revision=0,
                confirmed=True,
            )
        )

    outcomes = await asyncio.gather(
        apply("device-a", "slow"),
        apply("device-b", "fast"),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, ConfigurationConflictError) for item in outcomes) == 1
