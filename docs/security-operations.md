# Radcliffe security operations

This runbook covers the local single-owner deployment. Secrets belong in the
operator-managed environment, never in Git, Android diagnostics, conversation
envelopes, test reports, shell history, or command output. Keep `.env` files
mode `0600`, back them up separately from the repository, and use randomly
generated values rather than memorable development defaults.

Cloud model credentials remain disabled unless the user explicitly approves a
paid run. Credential rotation must not implicitly enable a provider.

## Credential inventory

| Credential | Holder | Purpose | Rotation consequence |
| --- | --- | --- | --- |
| Bootstrap token | Operator and unenrolled Android setup | Authorizes one-way device enrollment only | Existing enrolled devices continue working |
| Device credential | One Android installation, encrypted by Android Keystore | Authenticates catalog, timeline, context, and room-token requests | The old credential stops working immediately |
| Internal token | Control plane and operator | Worker credential issuance, device revocation, and auth-audit access | Restart internal callers with the new value |
| LiveKit API secret | LiveKit server and control plane | Signs short-lived room tokens | Existing room tokens stop verifying after the media server changes secret |
| Provider keys | Explicitly enabled local operator environment only | Optional paid model/speech access | Restart only the opted-in provider process |

Bootstrap, internal, LiveKit, and provider values should contain at least 32
random bytes. Room tokens expire in 60–3,600 seconds, are scoped to one room and
server-owned participant identity, and allow a client to publish microphone
audio only. Revoking a device blocks new control requests immediately; an
already-issued room token remains bounded by its short expiry, so an emergency
response should also remove that participant or rotate the LiveKit signing
secret.

## Safe device rotation

The Android/client-side rotation transaction is:

1. Generate a new 256-bit `rdc_dev_…` value locally.
2. Encrypt and persist the replacement before network I/O. Retain the current
   credential until the outcome is known.
3. Authenticate with the current credential and `POST` only the replacement to
   `/v1/devices/me/credential`.
4. If the response arrives, verify `/v1/devices/me` with the replacement, commit
   it locally, and delete the old ciphertext.
5. If the response is lost, probe `/v1/devices/me` with the replacement. A 200
   proves rotation committed; calling the rotation endpoint with the replacement
   as both current and requested value is an idempotent confirmation.
6. If the replacement is rejected but the old credential works, retry the same
   prepared replacement. If neither works, use operator recovery below.

The server updates the digest atomically, never stores or echoes either raw
credential, rejects a replacement already owned by another installation, and
records one content-free `rotated` audit event.

## Revocation and recovery

For a lost or distrusted device, use the internal operator endpoint with the
target device ID and the internal bearer loaded from the protected environment:

```sh
curl --fail --request POST \
  --header "Authorization: Bearer ${RADCLIFFE_INTERNAL_TOKEN}" \
  "http://127.0.0.1:8000/v1/devices/${DEVICE_ID}/revoke"
```

Do not use shell tracing while running credential-bearing commands. Revocation
is idempotent. Confirm that the old device gets HTTP 401 from both
`/v1/devices/me` and `/v1/sessions`.

If Android loses or corrupts its Keystore entry, revoke the old device when its
ID is known, enter the current bootstrap token in Connection settings, and
enroll as a new installation. Do not restore an exported raw device credential.
If a rotation response was merely lost, follow the probe procedure above before
re-enrolling.

Read the content-free audit through the internal endpoint:

```sh
curl --fail \
  --header "Authorization: Bearer ${RADCLIFFE_INTERNAL_TOKEN}" \
  "http://127.0.0.1:8000/v1/internal/device-auth-audit"
```

Expected event types are `enrolled`, `enrollment_conflict`,
`authentication_failed`, `rotated`, and `revoked`. The response must contain
IDs, reason codes, and timestamps only—never authorization headers, credentials,
room tokens, source text, or conversation content.

## Operator-secret rotation

- **Bootstrap:** generate a replacement in the protected environment and restart
  the control plane. Verify ordinary session routes still reject it and existing
  devices still authenticate. Distribute it only during deliberate re-enrollment.
- **Internal token:** update the control plane and every internal caller as one
  maintenance operation. Verify public/device credentials cannot call internal
  endpoints before or after rotation.
- **LiveKit API secret:** generate at least 32 random bytes; update the LiveKit
  server configuration and protected control-plane environment together;
  restart LiveKit, control plane, and managed workers; then run worker-dispatch
  and voice conformance. Expect existing rooms/tokens to reconnect with newly
  issued credentials.
- **Provider key:** keep cloud mode disabled, replace the protected value, and
  perform a paid probe only when the user explicitly requests it. Never place a
  provider key in a report fixture.

After any operator-secret rotation, run:

```sh
make check-worker-dispatch-local
make check-voice-local
uv run python scripts/device-auth-conformance.py
```

The device-auth report performs an exact byte scan of its database, logs, and
JSON report for every synthetic bootstrap, internal, device, LiveKit, and
provider secret used by the run.

## Backup and incident checks

Back up the SQLite database only while the control plane is stopped or through a
SQLite-consistent backup. The database contains conversation content, device
credential digests, tool audit, and auth audit; protect it as private data even
though it contains no raw bearer credentials. After restore, verify schema
migrations, device authentication, session timeline replay, and credential
rotation on an isolated copy before replacing the live database.

For an authentication incident, preserve content-free control logs and the auth
audit, revoke affected devices, rotate bootstrap/internal/LiveKit secrets as
appropriate, restart supervised services, and run the independent conformance
suite. Do not copy conversation bodies or raw `.env` values into the incident
report.
