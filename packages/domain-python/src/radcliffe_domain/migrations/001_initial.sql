PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS envelopes (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    message_id TEXT NOT NULL UNIQUE,
    content_sha256 BLOB NOT NULL,
    envelope BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence)
);

CREATE TABLE IF NOT EXISTS playback_acknowledgements (
    acknowledgement_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    status TEXT NOT NULL,
    position_ms INTEGER NOT NULL,
    boundary_index INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS playback_segment_order
ON playback_acknowledgements(session_id, segment_id, occurred_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json BLOB NOT NULL,
    grant_id TEXT NOT NULL,
    authorization TEXT NOT NULL,
    outcome TEXT NOT NULL,
    resolved_path TEXT,
    content_sha256 BLOB,
    result_json BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
