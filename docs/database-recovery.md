# SQLite backup, restore, and rollback

Radcliffe's SQLite database is private application state. It contains complete
conversation envelopes, source/tool results, playback positions, device
credential digests, and authentication audit rows. Backups stay under
`var/backups` by default, are excluded from Git and agent-share, and use mode
`0700` directories with `0600` database and metadata files.

The recovery tools never copy `var/.env`. Provider keys, bootstrap/internal
tokens, LiveKit signing secrets, and device credentials do not belong in a
backup manifest. A manifest contains only a backup ID, timestamps, source and
optional target Git revisions, database byte count and SHA-256, applied schema
versions, and row counts for the durable tables. Before metadata is emitted, it
is scanned against configured secret values.

## Routine backup and verification

Create an online backup while conversations may be active:

```sh
make backup-local
```

The command uses SQLite's online backup API rather than copying the live file
or its WAL. It writes into a private pending directory, fsyncs and checks the
copy, publishes it atomically, then verifies a scratch copy. Verification
requires:

- manifest byte count and SHA-256 match;
- SQLite quick integrity and foreign-key checks pass;
- applied migrations and table inventory match the manifest;
- the schema is not newer than the current checkout;
- current migrations apply successfully to a scratch copy;
- the current control plane starts and reports healthy from that copy; and
- session, envelope, tool, playback, device, and auth-audit row counts survive.

`make check-recovery-local` performs the same flow with a `drill` label. Neither
command enables a cloud provider or invokes a model.

The default retention is the newest seven valid backups. Invalid directories
and pending work are never pruned. Preview exactly which IDs are stale:

```sh
make recovery-prune-plan-local
```

Apply the displayed bounded policy explicitly:

```sh
scripts/run-local-recovery.sh prune --retain 7 --apply
```

## Isolated restore

List `var/backups` to select an opaque `bkp_...` identifier, then verify or
restore it to a non-live path:

```sh
scripts/run-local-recovery.sh verify bkp_EXACT_ID
scripts/run-local-recovery.sh restore bkp_EXACT_ID \
  --destination artifacts/recovery/restored.sqlite3
```

Restore verifies both the immutable backup and an independently copied staging
file before one atomic rename. A corrupt backup, migration/startup failure,
insufficient disk, or interrupted copy leaves the destination unchanged.
Ordinary `restore` resolves symlinks and refuses the configured live database
even if the caller uses a different spelling of its path.

## Upgrade receipt

Before deploying a known commit, create and verify a preflight backup. The
target is normalized to its full commit ID:

```sh
scripts/run-local-recovery.sh pre-upgrade --target-revision COMMIT
```

After deploying that exact revision and passing readiness/worker checks, record
the observed deployed revision beside the immutable preflight:

```sh
scripts/run-local-recovery.sh complete-upgrade bkp_EXACT_ID \
  --deployed-revision FULL_COMMIT_ID
```

The receipt is refused if either the preflight target or current checkout does
not exactly match. It contains no environment values and is mode `0600`.

## Confirmed live rollback

Live rollback is intentionally not an alias for restore. It requires a clean
tracked worktree and the backup ID twice:

```sh
scripts/run-local-recovery.sh rollback bkp_EXACT_ID \
  --confirm bkp_EXACT_ID
```

Before stopping anything, the command verifies the selected backup and recorded
release, then creates a new safety backup of current live state. It stops the
sidecar, control plane, and compatibility worker in reverse order, detaches the
checkout at the backup's recorded source revision, atomically restores the
database, and installs the release's supervised services. If checkout, restore,
or service installation fails, it restores the safety database, original
revision, and services. While services are stopped, rollback also retires the
previous database's WAL/SHM sidecars so newer transactions cannot replay into
the selected snapshot. If automatic recovery itself fails, stop and preserve
`var/backups`; do not retry by copying SQLite/WAL files manually.

After a successful rollback, require:

```sh
make check-operations-local
make check-worker-dispatch-local
```

Run voice conformance as well before treating a rolled-back build as a release.
