ALTER TABLE sessions ADD COLUMN worker_mode TEXT NOT NULL DEFAULT 'external'
CHECK (worker_mode IN ('external', 'managed'));

ALTER TABLE sessions ADD COLUMN worker_status TEXT NOT NULL DEFAULT 'stopped'
CHECK (worker_status IN ('stopped', 'starting', 'running', 'failed'));

ALTER TABLE sessions ADD COLUMN worker_updated_at TEXT;

CREATE INDEX IF NOT EXISTS managed_session_activity
ON sessions(principal_id, worker_mode, status, last_activity_at DESC);
