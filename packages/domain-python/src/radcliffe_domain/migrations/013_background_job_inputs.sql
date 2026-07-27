CREATE TABLE background_job_inputs (
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0 AND position < 8),
    kind TEXT NOT NULL CHECK (kind IN ('source', 'artifact')),
    name TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    producer_job_id TEXT,
    entity_id TEXT,
    artifact_id TEXT,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_length INTEGER NOT NULL CHECK (
        byte_length > 0 AND byte_length <= 8388608
    ),
    canonical_bytes BLOB NOT NULL,
    PRIMARY KEY (job_id, position),
    UNIQUE (job_id, name),
    FOREIGN KEY (job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
);
