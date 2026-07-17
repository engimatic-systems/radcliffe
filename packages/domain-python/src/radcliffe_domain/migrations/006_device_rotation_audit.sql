DROP INDEX device_auth_audit_order;

ALTER TABLE device_auth_audit RENAME TO device_auth_audit_before_rotation;

CREATE TABLE device_auth_audit (
    event_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    device_id TEXT,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'enrolled',
            'enrollment_conflict',
            'authentication_failed',
            'revoked',
            'rotated'
        )
    ),
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

INSERT INTO device_auth_audit(
    event_id, owner_id, device_id, event_type, reason, occurred_at
)
SELECT event_id, owner_id, device_id, event_type, reason, occurred_at
FROM device_auth_audit_before_rotation;

DROP TABLE device_auth_audit_before_rotation;

CREATE INDEX device_auth_audit_order
ON device_auth_audit(owner_id, occurred_at, event_id);
