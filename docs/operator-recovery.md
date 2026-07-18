# Operator recovery and log retention

Run `make check-operations-local` first. Act on its stable failure code, change
one condition at a time, and never enable a paid provider as a recovery step.
After recovery, require readiness plus `make check-worker-dispatch-local`;
require voice conformance when media, speech, LiveKit, or a release revision
changed.

## Recovery procedures

| Condition | Safe response | Observable success | Escalation limit |
| --- | --- | --- | --- |
| Unhealthy dependency | Inspect the individual readiness codes and service status; restart only after correcting the named dependency. | `/readyz` and operations status are ready. | Stop after two failed restart cycles; preserve the diagnostic bundle. |
| Stuck worker | End/archive the affected session, run worker dispatch, then restart supervised services if cleanup does not occur. | Exactly one worker joins a new generated session and leaves on end. | Do not manually kill more than one orphan cycle; preserve worker status/log metadata. |
| Ollama/model cold start | Verify Ollama is local, the configured model exists, and wait up to 120 seconds for sidecar warm-up. | Sidecar health is 200 and a local turn completes. | Do not switch providers; escalate after two warm-up failures or memory pressure. |
| LiveKit disconnect | Check the LiveKit signal code, local container, LAN address, and TURN ticket; restart Radcliffe only after LiveKit is reachable. | Signal readiness and worker join pass; reconnect creates no duplicate turn. | Stop after two reconnect/restart cycles; do not rotate credentials speculatively. |
| Database lock/corruption | Stop mutation, create/preserve a recovery backup if integrity permits, and verify a selected backup on an isolated path. | Quick check, schema, isolated app startup, and durable row inventory pass. | Never replace live state with an unverified copy; escalate immediately on integrity failure. |
| Lost device credential | Revoke the lost device from a trusted enrolled device or local operator path, then enroll the installation again. | Old credential returns 401, new credential works, and auth audit records both actions. | Do not export credential digests or weaken enrollment; rotate bootstrap authority if compromise is suspected. |
| Upgrade rollback | Select the exact pre-upgrade backup/receipt and use the confirmation-bound rollback command. | Original database/revision pair is restored and readiness/dispatch pass. | Stop if automatic recovery reports incomplete; preserve `var/backups` and do not copy WAL files. |
| Disk pressure | Export a diagnostic bundle, run log maintenance, preview/apply backup retention, then remove only known ignored artifacts. | Disk code is healthy and all retained files remain owner-only. | Never delete the live DB, `.env`, source fixtures, newest backup, or unknown files; escalate below 5%/1 GB free. |

## Log lifecycle and privacy audit

Run the exact-content/credential audit with:

```sh
make audit-logs-local
```

It scans the three supervised service logs and their retained rotations,
managed-worker logs and rotations, telemetry, and the current diagnostic
bundle. It rejects configured secret bytes, raw durable message bodies,
canonical source text/lines, bearer/device/provider credential patterns,
NULs/non-UTF-8 binary or microphone audio, and any file accessible to
group/others. Reports contain logical categories and codes only, never matched
bytes or host paths.

Routine maintenance is:

```sh
make maintain-logs-local
```

The command stops supervised services in reverse dependency order so open file
descriptors cannot keep writing renamed logs, rotates files at 10 MiB, retains
at most five rotations for at most 14 days, removes managed-worker logs older
than 14 days, enforces `0700` operational directories and `0600` files, then
restarts services in dependency order even if maintenance raises. Any current
or rotated log containing protected content is deleted during the stopped
window regardless of size; other current logs below the threshold remain.
Diagnostic bundles are audited and permission-fixed but are not rotated by
this command; ignored artifact cleanup is explicit operator work.

Run `make check-operations-tabletop` to exercise mappings, rejection, and
bounded rotation entirely in temporary fixtures. The report contains no
injected value and is written privately under `artifacts/operations`.
