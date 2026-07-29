CREATE TABLE playback_attempts (
    playback_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    operation_id TEXT NOT NULL,
    cancellation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    boundary_index INTEGER NOT NULL CHECK (boundary_index >= 0),
    duration_ms INTEGER NOT NULL CHECK (duration_ms > 0 AND duration_ms <= 30000),
    canonical_text TEXT NOT NULL,
    source TEXT NOT NULL CHECK (
        source IN ('model', 'call', 'text_source', 'system')
    ),
    source_envelope BLOB,
    completes_source_boundary INTEGER NOT NULL CHECK (
        completes_source_boundary IN (0, 1)
    ),
    speech_rate_multiplier REAL NOT NULL CHECK (speech_rate_multiplier > 0),
    pcm_audio BLOB NOT NULL,
    receiver_epoch TEXT NOT NULL,
    credit_sequence INTEGER NOT NULL CHECK (credit_sequence > 0),
    attempt_sequence INTEGER NOT NULL CHECK (attempt_sequence > 0),
    status TEXT NOT NULL CHECK (
        status IN (
            'announced', 'accepted', 'started', 'position', 'completed',
            'flushed', 'failed', 'superseded', 'cancelled'
        )
    ),
    position_ms INTEGER NOT NULL DEFAULT 0 CHECK (position_ms >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_active_playback_attempt_per_session
ON playback_attempts(session_id)
WHERE status IN ('announced', 'accepted', 'started', 'position');

CREATE INDEX playback_attempt_session_order
ON playback_attempts(session_id, attempt_sequence, created_at);

CREATE TABLE voice_generations (
    session_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL CHECK (generation > 0),
    updated_at TEXT NOT NULL
);
