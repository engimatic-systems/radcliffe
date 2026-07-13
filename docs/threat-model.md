# Radcliffe Milestone 1 threat model

## Assets and trust boundaries

Radcliffe protects the user's microphone audio, transcripts, source files,
model credentials, LiveKit secrets, capability grants, and durable audit log.
The Android client, public media edge, control plane, media worker, Pi sidecar,
Tool Gateway, provider APIs, and source filesystem are separate trust zones.
Only the Tool Gateway may convert a model request into local I/O.

Model output, tool arguments, source text, filenames, URLs, metadata, and
replayed network packets are untrusted. A model request is intent, never
authority. A source document is data, never an instruction channel.

## Milestone 1 capability grant

The durable grant contains a random `grant_id`, `session_id`, absolute readable
roots, allowed tool names, issue and expiry times, and a revocation generation.
The gateway compares it to current session state for every call. Grants are
short lived, scoped to one session, stored without credentials, and denied
after expiration, revocation, session end, or generation change.

The only M1 tool is:

| Tool | Allowlist and constraints | Side effects | Retry | Audit |
| --- | --- | --- | --- | --- | --- |
| `read_source` | One UTF-8 `.md` path under an explicit readable root; maximum 1 MiB; regular files only; resolved target must remain below the resolved root | Read only | Safe only with the same call and idempotency IDs | Immutable arguments, grant, decision, resolved path, byte count, SHA-256, outcome, timings, and error code |

Arguments are copied through canonical JSON before validation. Calls execute
sequentially in M1. A duplicate `call_id` or idempotency key returns the first
durable result and never repeats work. Cancellation records a terminal outcome
even if an underlying read finishes concurrently.

## Confirmation matrix

| Action | M1 behavior | Future policy |
| --- | --- | --- |
| Read allowed Markdown source | Grant is sufficient | Same |
| Read outside grant or unsupported type | Deny | Require a newly issued grant |
| File write/delete, shell, download, archive extraction | Tool unavailable and deny | Explicit per-action confirmation plus narrow grant |
| Web request | Tool unavailable and deny | Domain allowlist, SSRF controls, size/type limit, redirect validation |
| Credential or privilege change | Tool unavailable and deny | Out-of-band user confirmation; never delegated to model text |

## Threats and controls

- **Prompt injection:** source and tool output remain typed `TEXT_SOURCE` or
  `CALL` envelopes. ContextProjection labels provenance and cannot execute text.
- **Traversal and symlink escape:** reject absolute user paths and `..`; resolve
  root and target; require the target to be a regular file contained by the
  resolved root. An attacker-controlled symlink that resolves outside is
  denied. Production workers should add OS sandboxing as defense in depth.
- **SSRF and malicious archives:** no M1 tool performs network access or archive
  expansion. A future fetcher must reject loopback, link-local, private,
  metadata, non-HTTP(S), DNS-rebinding, unsafe redirects, oversized or
  mismatched content, and nested archives before it is enabled.
- **Stale/replayed authority:** signed room tokens and grants expire quickly;
  every operation includes session generation and stable idempotency IDs;
  revoked generations cannot emit audio or tools. Secrets never enter client
  logs or envelopes.
- **Tool replay:** the audit store enforces unique call and idempotency IDs and
  persists a request before authorization or work begins.
- **Data retention:** audio is not stored by default. Envelopes and tool audit
  remain local until the user deletes the session. Secret envelopes are
  excluded from model context and diagnostic reports. Logs use IDs and hashes,
  not source bodies or provider keys.
- **Media-edge compromise:** LiveKit transports encrypted media but terminates
  it at participants; the edge receives short-lived room credentials, not
  model or filesystem credentials. Room grants restrict identity and room.

## Required security tests

M1 tests cover valid reads, absolute and relative traversal, sibling-prefix
confusion, symlink escape, unsupported extension, invalid UTF-8, oversized
content, stale/revoked/wrong-session grants, unknown tools, mutated arguments,
duplicate calls, cancellation races, secret-context redaction, reordered
envelopes, stale-generation output, malformed Protobuf, and replayed commands.

Before web or mutating tools ship, gates additionally cover IPv4/IPv6 SSRF,
DNS rebinding, redirect chains, decompression bombs, archive traversal,
confirmation expiry and binding, partial writes, rollback, and audit tampering.
