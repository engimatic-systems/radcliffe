#!/usr/bin/env python3
"""Credential-free executor fixture used only by the deployed feature preflight."""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        frame = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, OSError):
        return 1
    if not isinstance(frame, dict) or frame.get("type") != "start":
        return 1
    print(
        json.dumps(
            {
                "type": "event",
                "kind": "succeeded",
                "detailCode": "completed",
                "entityIds": [],
                "artifactIds": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
