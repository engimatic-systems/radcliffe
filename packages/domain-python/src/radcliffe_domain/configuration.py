from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

import aiosqlite


class ConfigurationError(ValueError):
    pass


class ConfigurationConflictError(ConfigurationError):
    pass


class ConfigurationConfirmationRequired(ConfigurationError):
    pass


class ConfigurationCorruptionError(ConfigurationError):
    pass


class ConfigurationKey(StrEnum):
    RESPONSE_DETAIL = "response_detail"
    DEFAULT_SPEECH_RATE = "default_speech_rate"


class ConfigurationScope(StrEnum):
    OPERATOR_DEFAULT = "operator_default"
    DEVICE = "device"
    SESSION = "session"
    TURN = "turn"


class ConfigurationMutationKind(StrEnum):
    SET = "set"
    RESET = "reset"
    UNDO = "undo"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: ConfigurationKey
    schema: str
    default: str
    allowed_values: tuple[str, ...]
    allowed_scopes: frozenset[ConfigurationScope]
    persistent_scopes: frozenset[ConfigurationScope]
    confirmation_by_scope: Mapping[ConfigurationScope, str]


_ALL_SCOPES = frozenset(ConfigurationScope)
_PERSISTENT_SCOPES = frozenset(
    {
        ConfigurationScope.OPERATOR_DEFAULT,
        ConfigurationScope.DEVICE,
        ConfigurationScope.SESSION,
    }
)

SETTING_DEFINITIONS: Mapping[ConfigurationKey, SettingDefinition] = MappingProxyType(
    {
        ConfigurationKey.RESPONSE_DETAIL: SettingDefinition(
            key=ConfigurationKey.RESPONSE_DETAIL,
            schema="enum",
            default="standard",
            allowed_values=("brief", "standard", "detailed"),
            allowed_scopes=_ALL_SCOPES,
            persistent_scopes=_PERSISTENT_SCOPES,
            confirmation_by_scope=MappingProxyType(
                {
                    ConfigurationScope.OPERATOR_DEFAULT: "operator",
                    ConfigurationScope.DEVICE: "user",
                    ConfigurationScope.SESSION: "none",
                    ConfigurationScope.TURN: "none",
                }
            ),
        ),
        ConfigurationKey.DEFAULT_SPEECH_RATE: SettingDefinition(
            key=ConfigurationKey.DEFAULT_SPEECH_RATE,
            schema="enum",
            default="1.0x",
            allowed_values=("0.75x", "1.0x", "1.25x", "1.5x"),
            allowed_scopes=_ALL_SCOPES,
            persistent_scopes=_PERSISTENT_SCOPES,
            confirmation_by_scope=MappingProxyType(
                {
                    ConfigurationScope.OPERATOR_DEFAULT: "operator",
                    ConfigurationScope.DEVICE: "user",
                    ConfigurationScope.SESSION: "none",
                    ConfigurationScope.TURN: "none",
                }
            ),
        ),
    }
)

_VALUE_SYNONYMS: Mapping[ConfigurationKey, Mapping[str, str]] = MappingProxyType(
    {
        ConfigurationKey.RESPONSE_DETAIL: MappingProxyType(
            {
                "brief": "brief",
                "concise": "brief",
                "short": "brief",
                "standard": "standard",
                "normal": "standard",
                "default": "standard",
                "detailed": "detailed",
                "detail": "detailed",
                "thorough": "detailed",
                "verbose": "detailed",
            }
        ),
        ConfigurationKey.DEFAULT_SPEECH_RATE: MappingProxyType(
            {
                "0.75x": "0.75x",
                "slow": "0.75x",
                "slowly": "0.75x",
                "1.0x": "1.0x",
                "1x": "1.0x",
                "normal": "1.0x",
                "regular": "1.0x",
                "default": "1.0x",
                "1.25x": "1.25x",
                "fast": "1.25x",
                "quick": "1.25x",
                "quickly": "1.25x",
                "1.5x": "1.5x",
                "very_fast": "1.5x",
                "very fast": "1.5x",
                "fastest": "1.5x",
            }
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ConfigurationContext:
    owner_id: str
    device_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None

    def target_id(self, scope: ConfigurationScope) -> str:
        if scope is ConfigurationScope.OPERATOR_DEFAULT:
            return "global"
        if scope is ConfigurationScope.DEVICE and self.device_id:
            return self.device_id
        if scope is ConfigurationScope.SESSION and self.session_id:
            return self.session_id
        if scope is ConfigurationScope.TURN and self.session_id and self.turn_id:
            return f"{self.session_id}:{self.turn_id}"
        raise ConfigurationError(f"{scope.value} scope requires its exact target")


@dataclass(frozen=True, slots=True)
class ConfigurationLayer:
    scope: str
    target_id: str
    value: str
    revision: int


@dataclass(frozen=True, slots=True)
class EffectiveConfiguration:
    definition: SettingDefinition
    value: str
    effective_scope: str
    revision: int
    layers: tuple[ConfigurationLayer, ...]


@dataclass(frozen=True, slots=True)
class ConfigurationMutation:
    mutation_id: str
    idempotency_key: str
    key: ConfigurationKey | str
    scope: ConfigurationScope | str
    kind: ConfigurationMutationKind | str
    context: ConfigurationContext
    actor_id: str
    value: str | None = None
    expected_revision: int | None = None
    undo_mutation_id: str | None = None
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ConfigurationMutationResult:
    mutation_id: str
    key: ConfigurationKey
    scope: ConfigurationScope
    target_id: str
    previous_value: str | None
    applied_value: str | None
    revision: int
    effective: EffectiveConfiguration
    replayed: bool = False


def configuration_key(value: ConfigurationKey | str) -> ConfigurationKey:
    try:
        return ConfigurationKey(value)
    except ValueError as error:
        raise ConfigurationError("setting is not registered") from error


def configuration_scope(value: ConfigurationScope | str) -> ConfigurationScope:
    try:
        return ConfigurationScope(value)
    except ValueError as error:
        raise ConfigurationError("setting scope is not registered") from error


def normalize_configuration_value(key: ConfigurationKey | str, value: object) -> str:
    selected = configuration_key(key)
    if not isinstance(value, str):
        raise ConfigurationError("setting value must be a registered string enum")
    normalized = value.strip().casefold().replace("-", "_")
    canonical = _VALUE_SYNONYMS[selected].get(normalized)
    if canonical is None:
        raise ConfigurationError("setting value is outside its registered bounds")
    return canonical


class ConfigurationRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def inspect(
        self,
        key: ConfigurationKey | str,
        context: ConfigurationContext,
    ) -> EffectiveConfiguration:
        selected = configuration_key(key)
        async with aiosqlite.connect(self.path) as database:
            return await self._inspect(database, selected, context)

    async def mutate(self, mutation: ConfigurationMutation) -> ConfigurationMutationResult:
        selected = configuration_key(mutation.key)
        scope = configuration_scope(mutation.scope)
        try:
            kind = ConfigurationMutationKind(mutation.kind)
        except ValueError as error:
            raise ConfigurationError("setting mutation is not registered") from error
        definition = SETTING_DEFINITIONS[selected]
        if scope not in definition.allowed_scopes:
            raise ConfigurationError("setting is not allowed at this scope")
        target_id = mutation.context.target_id(scope)
        confirmation = definition.confirmation_by_scope[scope]
        if confirmation != "none" and not mutation.confirmed:
            raise ConfigurationConfirmationRequired(
                f"{scope.value} changes require {confirmation} confirmation"
            )
        if not mutation.mutation_id or not mutation.idempotency_key or not mutation.actor_id:
            raise ConfigurationError("setting mutation identifiers are required")
        canonical_value = None
        if kind is ConfigurationMutationKind.SET:
            canonical_value = normalize_configuration_value(selected, mutation.value)
        elif mutation.value is not None:
            raise ConfigurationError("reset and undo mutations cannot include a value")
        if kind is ConfigurationMutationKind.UNDO and not mutation.undo_mutation_id:
            raise ConfigurationError("undo requires a prior mutation ID")
        if kind is not ConfigurationMutationKind.UNDO and mutation.undo_mutation_id is not None:
            raise ConfigurationError("only undo accepts a prior mutation ID")

        request_digest = self._request_digest(
            mutation,
            selected,
            scope,
            target_id,
            kind,
            canonical_value,
        )
        async with aiosqlite.connect(self.path) as database:
            await database.execute("BEGIN IMMEDIATE")
            replay = await (
                await database.execute(
                    "SELECT request_sha256, result_json FROM configuration_mutations "
                    "WHERE idempotency_key = ? OR mutation_id = ?",
                    (mutation.idempotency_key, mutation.mutation_id),
                )
            ).fetchone()
            if replay is not None:
                if bytes(replay[0]) != request_digest:
                    await database.rollback()
                    raise ConfigurationConflictError(
                        "configuration idempotency key was reused with different parameters"
                    )
                await database.rollback()
                return self._result_from_json(bytes(replay[1]), replayed=True)

            current = await self._current_row(
                database,
                mutation.context.owner_id,
                selected,
                scope,
                target_id,
            )
            previous_value = current[0] if current is not None else None
            current_revision = current[1] if current is not None else 0
            if mutation.expected_revision is not None and (
                mutation.expected_revision != current_revision
            ):
                await database.rollback()
                raise ConfigurationConflictError("configuration revision changed concurrently")

            applied_value = canonical_value
            if kind is ConfigurationMutationKind.RESET:
                applied_value = None
            elif kind is ConfigurationMutationKind.UNDO:
                assert mutation.undo_mutation_id is not None
                prior = await (
                    await database.execute(
                        "SELECT result_json FROM configuration_mutations WHERE mutation_id = ?",
                        (mutation.undo_mutation_id,),
                    )
                ).fetchone()
                if prior is None:
                    await database.rollback()
                    raise ConfigurationError("undo mutation does not exist")
                prior_result = self._result_from_json(bytes(prior[0]))
                if (
                    prior_result.key is not selected
                    or prior_result.scope is not scope
                    or prior_result.target_id != target_id
                ):
                    await database.rollback()
                    raise ConfigurationError("undo mutation controls another setting target")
                if current_revision != prior_result.revision:
                    await database.rollback()
                    raise ConfigurationConflictError(
                        "configuration changed after the requested undo point"
                    )
                applied_value = prior_result.previous_value

            revision = current_revision + 1
            now = datetime.now(UTC).isoformat()
            await database.execute(
                """INSERT INTO configuration_values(
                    owner_id, setting_key, scope, target_id, value, revision,
                    updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, setting_key, scope, target_id) DO UPDATE SET
                    value = excluded.value,
                    revision = excluded.revision,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at""",
                (
                    mutation.context.owner_id,
                    selected.value,
                    scope.value,
                    target_id,
                    applied_value,
                    revision,
                    mutation.actor_id,
                    now,
                ),
            )
            effective = await self._inspect(database, selected, mutation.context)
            result = ConfigurationMutationResult(
                mutation_id=mutation.mutation_id,
                key=selected,
                scope=scope,
                target_id=target_id,
                previous_value=previous_value,
                applied_value=applied_value,
                revision=revision,
                effective=effective,
            )
            result_json = self._result_json(result)
            await database.execute(
                "INSERT INTO configuration_mutations VALUES (?, ?, ?, ?, ?)",
                (
                    mutation.mutation_id,
                    mutation.idempotency_key,
                    request_digest,
                    result_json,
                    now,
                ),
            )
            await database.commit()
            return result

    async def _inspect(
        self,
        database: aiosqlite.Connection,
        key: ConfigurationKey,
        context: ConfigurationContext,
    ) -> EffectiveConfiguration:
        definition = SETTING_DEFINITIONS[key]
        layers: list[ConfigurationLayer] = []
        for scope in ConfigurationScope:
            try:
                target_id = context.target_id(scope)
            except ConfigurationError:
                continue
            current = await self._current_row(
                database,
                context.owner_id,
                key,
                scope,
                target_id,
            )
            if current is None or current[0] is None:
                continue
            value, revision = current
            assert value is not None
            layers.append(ConfigurationLayer(scope.value, target_id, value, revision))
        if not layers:
            return EffectiveConfiguration(definition, definition.default, "built_in", 0, ())
        effective = layers[-1]
        return EffectiveConfiguration(
            definition,
            effective.value,
            effective.scope,
            effective.revision,
            tuple(layers),
        )

    @staticmethod
    async def _current_row(
        database: aiosqlite.Connection,
        owner_id: str,
        key: ConfigurationKey,
        scope: ConfigurationScope,
        target_id: str,
    ) -> tuple[str | None, int] | None:
        row = await (
            await database.execute(
                """SELECT value, revision FROM configuration_values
                WHERE owner_id = ? AND setting_key = ? AND scope = ? AND target_id = ?""",
                (owner_id, key.value, scope.value, target_id),
            )
        ).fetchone()
        if row is None:
            return None
        value = str(row[0]) if row[0] is not None else None
        revision = int(row[1])
        if revision < 1:
            raise ConfigurationCorruptionError("configuration revision is corrupt")
        if value is not None:
            try:
                canonical = normalize_configuration_value(key, value)
            except ConfigurationError as error:
                raise ConfigurationCorruptionError("configuration value is corrupt") from error
            if canonical != value:
                raise ConfigurationCorruptionError("configuration value is corrupt")
        return value, revision

    @staticmethod
    def _request_digest(
        mutation: ConfigurationMutation,
        key: ConfigurationKey,
        scope: ConfigurationScope,
        target_id: str,
        kind: ConfigurationMutationKind,
        value: str | None,
    ) -> bytes:
        payload = json.dumps(
            {
                "mutation_id": mutation.mutation_id,
                "idempotency_key": mutation.idempotency_key,
                "owner_id": mutation.context.owner_id,
                "actor_id": mutation.actor_id,
                "key": key.value,
                "scope": scope.value,
                "target_id": target_id,
                "kind": kind.value,
                "value": value,
                "expected_revision": mutation.expected_revision,
                "undo_mutation_id": mutation.undo_mutation_id,
                "confirmed": mutation.confirmed,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).digest()

    @staticmethod
    def _result_json(result: ConfigurationMutationResult) -> bytes:
        return json.dumps(
            {
                "mutation_id": result.mutation_id,
                "key": result.key.value,
                "scope": result.scope.value,
                "target_id": result.target_id,
                "previous_value": result.previous_value,
                "applied_value": result.applied_value,
                "revision": result.revision,
                "effective": {
                    "value": result.effective.value,
                    "effective_scope": result.effective.effective_scope,
                    "revision": result.effective.revision,
                    "layers": [
                        {
                            "scope": layer.scope,
                            "target_id": layer.target_id,
                            "value": layer.value,
                            "revision": layer.revision,
                        }
                        for layer in result.effective.layers
                    ],
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def _result_from_json(
        value: bytes,
        *,
        replayed: bool = False,
    ) -> ConfigurationMutationResult:
        payload = json.loads(value)
        key = configuration_key(payload["key"])
        effective_payload = payload["effective"]
        effective = EffectiveConfiguration(
            definition=SETTING_DEFINITIONS[key],
            value=normalize_configuration_value(key, effective_payload["value"]),
            effective_scope=str(effective_payload["effective_scope"]),
            revision=int(effective_payload["revision"]),
            layers=tuple(
                ConfigurationLayer(
                    scope=str(layer["scope"]),
                    target_id=str(layer["target_id"]),
                    value=normalize_configuration_value(key, layer["value"]),
                    revision=int(layer["revision"]),
                )
                for layer in effective_payload["layers"]
            ),
        )
        return ConfigurationMutationResult(
            mutation_id=str(payload["mutation_id"]),
            key=key,
            scope=configuration_scope(payload["scope"]),
            target_id=str(payload["target_id"]),
            previous_value=(
                normalize_configuration_value(key, payload["previous_value"])
                if payload["previous_value"] is not None
                else None
            ),
            applied_value=(
                normalize_configuration_value(key, payload["applied_value"])
                if payload["applied_value"] is not None
                else None
            ),
            revision=int(payload["revision"]),
            effective=effective,
            replayed=replayed,
        )
