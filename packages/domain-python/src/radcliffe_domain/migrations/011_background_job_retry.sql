ALTER TABLE background_job_commands RENAME TO background_job_commands_v10;

CREATE TABLE background_job_commands (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 BLOB NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('follow_up', 'set_priority', 'cancel', 'retry')),
    result_revision INTEGER NOT NULL CHECK (result_revision > 0),
    occurred_at TEXT NOT NULL,
    UNIQUE (job_id, idempotency_key),
    FOREIGN KEY (job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
);

INSERT INTO background_job_commands(
    command_id, job_id, idempotency_key, request_sha256,
    kind, result_revision, occurred_at
)
SELECT
    command_id, job_id, idempotency_key, request_sha256,
    kind, result_revision, occurred_at
FROM background_job_commands_v10;

DROP TABLE background_job_commands_v10;
