# Bounded playback model

`BoundedPlayback.tla` is the executable safety model for Radcliffe's receiver-credit
protocol. The checked configuration explores three ordered semantic segments,
two generations, temporary data/media disconnection, sender and receiver
process restart, cancellation, reissue, duplicate/stale delivery opportunities,
and a one-attempt receiver window.

Run it with:

```sh
make check-playback-model
```

The runner downloads the pinned TLA+ tools release to the user's cache, verifies
its SHA-256 digest, and never adds the tool JAR to the repository.

The safety invariants checked by TLC are:

- at most one admitted nonterminal attempt and exact credit conservation;
- realtime media cannot start before Android metadata acceptance;
- only the next canonical segment can be admitted or accepted;
- only the current generation can retain an outstanding attempt;
- cancellation retires the outstanding attempt before another generation begins.

The human-readable refinement mapping, total event table, environmental fairness
assumptions, and conditional-liveness argument are in
`docs/bounded-playback-protocol.md`.
