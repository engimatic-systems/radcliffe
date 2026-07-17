CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    installation_id TEXT NOT NULL,
    participant_identity TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    credential_sha256 BLOB NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_authenticated_at TEXT,
    revoked_at TEXT,
    UNIQUE(owner_id, installation_id)
);

CREATE INDEX device_owner_status
ON devices(owner_id, revoked_at, created_at);

CREATE TABLE device_auth_audit (
    event_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    device_id TEXT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('enrolled', 'enrollment_conflict', 'authentication_failed', 'revoked')
    ),
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX device_auth_audit_order
ON device_auth_audit(owner_id, occurred_at, event_id);
