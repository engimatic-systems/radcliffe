CREATE TABLE background_job_attempt_contexts (
    job_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK(attempt > 0),
    user_message TEXT NOT NULL,
    related_links_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY(job_id, attempt),
    FOREIGN KEY(job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
);

