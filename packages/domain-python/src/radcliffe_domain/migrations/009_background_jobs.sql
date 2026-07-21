CREATE TABLE background_jobs (
    job_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    idempotency_key TEXT NOT NULL,
    request_sha256 BLOB NOT NULL,
    instruction TEXT NOT NULL,
    notification_policy TEXT NOT NULL CHECK (
        notification_policy IN ('immediate', 'next_safe_pause', 'silent', 'user_waiting')
    ),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 4),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')
    ),
    budget_microusd INTEGER CHECK (budget_microusd > 0),
    max_runtime_seconds INTEGER NOT NULL CHECK (max_runtime_seconds > 0),
    revision INTEGER NOT NULL CHECK (revision > 0),
    cancel_requested_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (principal_id, idempotency_key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX background_job_session_order
ON background_jobs(principal_id, session_id, created_at DESC, job_id DESC);

CREATE INDEX background_job_claim_order
ON background_jobs(status, priority, created_at, lease_expires_at);

CREATE TABLE background_job_follow_ups (
    follow_up_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 BLOB NOT NULL,
    instruction TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (job_id, idempotency_key),
    FOREIGN KEY (job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE background_job_commands (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 BLOB NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('follow_up', 'set_priority', 'cancel')),
    result_revision INTEGER NOT NULL CHECK (result_revision > 0),
    occurred_at TEXT NOT NULL,
    UNIQUE (job_id, idempotency_key),
    FOREIGN KEY (job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE background_job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'accepted', 'started', 'lease_recovered', 'progress', 'waiting',
            'follow_up_added', 'priority_changed', 'cancellation_requested',
            'cancelled', 'succeeded', 'failed'
        )
    ),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')
    ),
    notification_policy TEXT NOT NULL CHECK (
        notification_policy IN ('immediate', 'next_safe_pause', 'silent', 'user_waiting')
    ),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 4),
    detail_code TEXT NOT NULL,
    entity_ids_json TEXT NOT NULL DEFAULT '[]',
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    occurred_at TEXT NOT NULL,
    UNIQUE (job_id, revision),
    FOREIGN KEY (job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX background_job_event_replay
ON background_job_events(job_id, revision);
