ALTER TABLE playback_acknowledgements
ADD COLUMN playback_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS playback_attempt_order
ON playback_acknowledgements(session_id, playback_id, occurred_at);
