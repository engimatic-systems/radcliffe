CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended', 'archived')),
    protocol_version INTEGER NOT NULL CHECK (protocol_version > 0),
    generation INTEGER NOT NULL CHECK (generation > 0),
    UNIQUE (principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS session_catalog_recency
ON sessions(principal_id, last_activity_at DESC, session_id DESC);

CREATE TRIGGER IF NOT EXISTS touch_session_after_envelope
AFTER INSERT ON envelopes
BEGIN
    UPDATE sessions
    SET last_activity_at = MAX(last_activity_at, NEW.created_at)
    WHERE session_id = NEW.session_id;
END;

CREATE TRIGGER IF NOT EXISTS touch_session_after_playback
AFTER INSERT ON playback_acknowledgements
BEGIN
    UPDATE sessions
    SET last_activity_at = MAX(last_activity_at, NEW.occurred_at)
    WHERE session_id = NEW.session_id;
END;

CREATE TRIGGER IF NOT EXISTS touch_session_after_tool_call
AFTER INSERT ON tool_calls
BEGIN
    UPDATE sessions
    SET last_activity_at = MAX(last_activity_at, NEW.created_at)
    WHERE session_id = NEW.session_id;
END;
