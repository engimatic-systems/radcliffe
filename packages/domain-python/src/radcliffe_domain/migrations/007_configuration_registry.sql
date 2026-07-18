CREATE TABLE configuration_values (
    owner_id TEXT NOT NULL,
    setting_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (
        scope IN ('operator_default', 'device', 'session', 'turn')
    ),
    target_id TEXT NOT NULL,
    value TEXT,
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, setting_key, scope, target_id)
);

CREATE INDEX configuration_effective_lookup
ON configuration_values(owner_id, setting_key, scope, target_id);

CREATE TABLE configuration_mutations (
    mutation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_sha256 BLOB NOT NULL,
    result_json BLOB NOT NULL,
    occurred_at TEXT NOT NULL
);
