CREATE TABLE session_transitions (
    request_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_sha256 BLOB NOT NULL,
    principal_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_generation INTEGER NOT NULL CHECK (source_generation > 0),
    action TEXT NOT NULL CHECK (action IN ('create', 'switch', 'restart', 'end')),
    destination_session_id TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (source_session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (destination_session_id) REFERENCES sessions(session_id)
);

CREATE INDEX session_transition_source_lookup
ON session_transitions(principal_id, source_session_id, occurred_at);
