#!/usr/bin/env python3
"""Deterministic, content-free schedule harness for bounded playback."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final

MAX_SEGMENT_DURATION_MS: Final = 30_000


@dataclass(slots=True)
class Attempt:
    segment: int
    playback: int
    duration_ms: int
    accepted: bool = False
    started: bool = False
    completed: bool = False
    position_ms: int = 0


@dataclass(slots=True)
class Harness:
    generation: int = 1
    next_segment: int = 1
    next_playback: int = 1
    credit: int = 1
    outstanding: Attempt | None = None
    heard_through: int = 0
    connected: bool = True
    trace: list[dict[str, object]] = field(default_factory=list)
    maximum_depth: int = 0
    maximum_duration_ms: int = 0

    def apply(self, action: str, value: int = 0) -> None:
        handler = getattr(self, f"_on_{action}")
        outcome = handler(value)
        depth = int(self.outstanding is not None)
        duration = self.outstanding.duration_ms if self.outstanding is not None else 0
        self.maximum_depth = max(self.maximum_depth, depth)
        self.maximum_duration_ms = max(self.maximum_duration_ms, duration)
        self._check()
        self.trace.append(
            {
                "step": len(self.trace) + 1,
                "action": action,
                "value": value,
                "outcome": outcome or "applied",
                "generation": self.generation,
                "segment": self.outstanding.segment if self.outstanding else None,
                "playback": self.outstanding.playback if self.outstanding else None,
                "credit": self.credit,
                "heard_through": self.heard_through,
                "connected": self.connected,
            }
        )

    def _on_delay(self, value: int) -> str:
        if value < 0:
            raise ValueError("delay must be non-negative")
        return "time-elapsed-without-credit"

    def _on_admit(self, value: int) -> None:
        duration = value or 1_000
        if self.credit != 1 or self.outstanding is not None:
            raise AssertionError("admission attempted without receiver credit")
        if not 0 < duration <= MAX_SEGMENT_DURATION_MS:
            raise AssertionError("segment exceeds receiver capacity")
        self.outstanding = Attempt(self.next_segment, self.next_playback, duration)
        self.next_playback += 1
        self.credit = 0

    def _on_metadata(self, _value: int) -> None:
        attempt = self._require_attempt()
        attempt.accepted = True

    def _on_media(self, _value: int) -> str | None:
        attempt = self._require_attempt()
        if not attempt.accepted:
            return "blocked-until-metadata-accepted"
        return None

    def _on_start(self, _value: int) -> None:
        attempt = self._require_attempt()
        if not attempt.accepted:
            raise AssertionError("decoded PCM started before metadata acceptance")
        attempt.started = True

    def _on_position(self, value: int) -> None:
        attempt = self._require_attempt()
        if not attempt.started or value < attempt.position_ms or value > attempt.duration_ms:
            raise AssertionError("invalid decoded-PCM position")
        attempt.position_ms = value

    def _on_complete(self, _value: int) -> None:
        attempt = self._require_attempt()
        if not attempt.started:
            raise AssertionError("completion arrived before decoded PCM started")
        attempt.position_ms = attempt.duration_ms
        attempt.completed = True

    def _on_ack(self, _value: int) -> str | None:
        attempt = self._require_attempt()
        if not attempt.completed:
            raise AssertionError("terminal credit returned without decoded-PCM completion")
        if attempt.segment <= self.heard_through:
            return "duplicate-terminal-idempotent"
        if attempt.segment != self.heard_through + 1:
            raise AssertionError("heard boundary advanced out of order")
        self.heard_through = attempt.segment
        self.next_segment = attempt.segment + 1
        self.outstanding = None
        self.credit = 1
        return None

    def _on_duplicate_ack(self, _value: int) -> str:
        return "duplicate-terminal-idempotent"

    def _on_disconnect(self, _value: int) -> None:
        self.connected = False

    def _on_reconnect(self, _value: int) -> str:
        self.connected = True
        attempt = self.outstanding
        if attempt is None:
            return "ready"
        if attempt.completed:
            self._on_ack(0)
            return "terminal-reconciled"
        attempt.playback = self.next_playback
        self.next_playback += 1
        attempt.accepted = False
        attempt.started = False
        attempt.completed = False
        attempt.position_ms = 0
        return "same-segment-new-attempt"

    def _on_worker_restart(self, value: int) -> str:
        self.connected = False
        return self._on_reconnect(value)

    def _on_receiver_restart(self, value: int) -> str:
        self.connected = False
        return self._on_reconnect(value)

    def _on_route_change(self, value: int) -> str:
        return self._on_reconnect(value)

    def _on_background(self, _value: int) -> None:
        self.connected = False

    def _on_foreground(self, value: int) -> str:
        return self._on_reconnect(value)

    def _on_cancel(self, _value: int) -> None:
        self.outstanding = None
        self.credit = 1
        self.generation += 1

    def _require_attempt(self) -> Attempt:
        if self.outstanding is None:
            raise AssertionError("schedule requires an outstanding attempt")
        return self.outstanding

    def _check(self) -> None:
        if self.credit not in {0, 1}:
            raise AssertionError("credit left its finite domain")
        if (self.outstanding is None) != (self.credit == 1):
            raise AssertionError("credit is not conserved")
        if self.outstanding is not None:
            if self.outstanding.segment != self.next_segment:
                raise AssertionError("only the next canonical segment may be outstanding")
            if self.outstanding.duration_ms > MAX_SEGMENT_DURATION_MS:
                raise AssertionError("admitted-unheard duration exceeded the bound")
        if self.next_segment != self.heard_through + 1:
            raise AssertionError("canonical and heard boundaries diverged")


SCENARIOS: Final[dict[str, tuple[tuple[str, int], ...]]] = {
    "immediate": (
        ("admit", 1_000),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "slow_synthesis": (
        ("delay", 2_000),
        ("admit", 1_000),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "delayed_android_consumption": (
        ("admit", 2_000),
        ("metadata", 0),
        ("media", 0),
        ("delay", 5_000),
        ("start", 0),
        ("position", 1_000),
        ("complete", 0),
        ("ack", 0),
    ),
    "media_before_metadata": (
        ("admit", 1_000),
        ("media", 0),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "media_delay": (
        ("admit", 1_000),
        ("metadata", 0),
        ("delay", 3_000),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "disconnect_reconnect": (
        ("admit", 1_000),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("disconnect", 0),
        ("delay", 8_000),
        ("reconnect", 0),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "worker_restart": (
        ("admit", 1_000),
        ("metadata", 0),
        ("worker_restart", 0),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "receiver_restart": (
        ("admit", 1_000),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("receiver_restart", 0),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "background_foreground": (
        ("admit", 1_000),
        ("metadata", 0),
        ("background", 0),
        ("foreground", 0),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "bluetooth_route_change": (
        ("admit", 1_000),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("route_change", 0),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("complete", 0),
        ("ack", 0),
    ),
    "cancellation": (
        ("admit", 1_000),
        ("metadata", 0),
        ("media", 0),
        ("start", 0),
        ("cancel", 0),
        ("delay", 8_000),
    ),
}


def run_scenarios() -> dict[str, object]:
    results: dict[str, object] = {}
    maximum_depth = 0
    maximum_duration_ms = 0
    for name, schedule in SCENARIOS.items():
        harness = Harness()
        for action, value in schedule:
            harness.apply(action, value)
        maximum_depth = max(maximum_depth, harness.maximum_depth)
        maximum_duration_ms = max(maximum_duration_ms, harness.maximum_duration_ms)
        results[name] = {
            "schedule": [{"action": action, "value": value} for action, value in schedule],
            "trace": harness.trace,
            "final": asdict(harness)
            | {
                "outstanding": asdict(harness.outstanding)
                if harness.outstanding is not None
                else None
            },
        }
    return {
        "schema_version": 1,
        "content_free": True,
        "receiver_window": 1,
        "max_segment_duration_ms": MAX_SEGMENT_DURATION_MS,
        "scenario_count": len(results),
        "maximum_observed_admitted_depth": maximum_depth,
        "maximum_observed_admitted_duration_ms": maximum_duration_ms,
        "scenarios": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_scenarios()
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(
        f"{report['scenario_count']} schedules passed; "
        f"max depth={report['maximum_observed_admitted_depth']}; "
        f"max duration_ms={report['maximum_observed_admitted_duration_ms']}"
    )


if __name__ == "__main__":
    main()
