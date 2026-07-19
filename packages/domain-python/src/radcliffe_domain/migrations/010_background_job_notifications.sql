ALTER TABLE background_job_events ADD COLUMN delivery_sequence INTEGER;

UPDATE background_job_events
SET delivery_sequence = rowid
WHERE delivery_sequence IS NULL;

CREATE UNIQUE INDEX background_job_event_delivery_sequence
ON background_job_events(delivery_sequence);

CREATE INDEX background_job_session_delivery
ON background_job_events(delivery_sequence, job_id);

CREATE TABLE background_job_notification_deliveries (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    claim_owner TEXT,
    disposition TEXT NOT NULL CHECK (
        disposition IN (
            'announcing', 'spoken', 'interrupted', 'failed', 'silent', 'visibility_only'
        )
    ),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES background_job_events(event_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX background_job_notification_session
ON background_job_notification_deliveries(session_id, updated_at, event_id);
