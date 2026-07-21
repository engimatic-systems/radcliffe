#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    response_detail: str = "brief"
    expected_literal: str | None = None
    expected_numbers: tuple[int, ...] = ()
    expected_slow_tool: bool = False


COUNT = tuple(range(1, 61))
CASES = (
    Case("count_brief", "Count from 1 to 60.", "brief", expected_numbers=COUNT),
    Case("count_standard", "Count from 1 to 60.", "standard", expected_numbers=COUNT),
    Case("count_detailed", "Count from 1 to 60.", "detailed", expected_numbers=COUNT),
    Case(
        "count_slowly",
        "Count slowly from 1 to 60.",
        expected_numbers=COUNT,
        expected_slow_tool=True,
    ),
    Case("identifier", 'Repeat the ID exactly: "build-A7:42"', expected_literal="build-A7:42"),
    Case(
        "hash",
        "Say this hash exactly: aabbccddeeff00112233445566778899",
        expected_literal="aabbccddeeff00112233445566778899",
    ),
    Case(
        "url",
        "Repeat exactly https://example.test/a?q=1",
        expected_literal="https://example.test/a?q=1",
    ),
    Case(
        "json",
        'Repeat this JSON exactly: {"ready":true,"count":2}',
        expected_literal='{"ready":true,"count":2}',
    ),
    Case(
        "path",
        "Say this path exactly: /srv/radcliffe/config.json",
        expected_literal="/srv/radcliffe/config.json",
    ),
    Case(
        "timestamp",
        "Repeat exactly 2026-07-21T04:00:00Z",
        expected_literal="2026-07-21T04:00:00Z",
    ),
    Case(
        "characters",
        'Spell out "Q7-x" character by character',
        expected_literal="Q, 7, -, x",
    ),
    Case(
        "verbatim",
        'Repeat verbatim: "No compression allowed"',
        expected_literal="No compression allowed",
    ),
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the content-free exact-output corpus against one sidecar."
    )
    parser.add_argument("--sidecar-url", default="http://127.0.0.1:8787")
    parser.add_argument("--control-url", default="http://127.0.0.1:8000")
    parser.add_argument("--profile", choices=("qwen", "haiku"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def turn_request(case: Case, session_id: str) -> dict[str, object]:
    nonce = secrets.token_hex(12)
    return {
        "requestId": f"exact-{case.name}-{nonce}",
        "sessionId": session_id,
        "generation": 1,
        "operationId": f"exact-operation-{nonce}",
        "cancellationId": f"exact-cancellation-{nonce}",
        "traceId": "trc_" + secrets.token_hex(16),
        "responseDetail": case.response_detail,
        "packets": [
            {
                "projectionVersion": "context-v2",
                "messageId": f"exact-message-{nonce}",
                "role": "user",
                "content": case.prompt,
                "source": "user",
                "annotations": [["radcliffe.turn_id", f"exact-turn-{nonce}"]],
                "include": True,
                "timestamp": int(datetime.now(UTC).timestamp() * 1000),
            }
        ],
    }


def validate_case(case: Case, events: list[dict[str, Any]]) -> dict[str, object]:
    reasons: list[str] = []
    completes = [event for event in events if event.get("type") == "model_complete"]
    sidecar_completed = any(event.get("type") == "complete" for event in events)
    if len(completes) != 1 or not sidecar_completed:
        reasons.append("turn_incomplete")
        output = ""
    else:
        output = str(completes[0].get("text", ""))
    if case.expected_numbers:
        observed = tuple(int(value) for value in re.findall(r"\b\d+\b", output))
        if observed != case.expected_numbers:
            reasons.append("numeric_sequence")
        if "..." in output or "…" in output or re.search(r"\band so (?:on|forth)\b", output, re.I):
            reasons.append("abbreviated")
    elif output != case.expected_literal:
        reasons.append("literal")
    tool_starts = [event for event in events if event.get("type") == "tool_start"]
    tool_ends = [event for event in events if event.get("type") == "tool_end"]
    if case.expected_slow_tool:
        matching = [
            event
            for event in tool_starts
            if event.get("toolName") == "configure"
            and event.get("arguments")
            == {
                "operation": "set",
                "key": "default_speech_rate",
                "scope": "turn",
                "value": "0.75x",
            }
        ]
        if len(matching) != 1:
            reasons.append("slow_delivery_tool")
        elif not any(
            event.get("callId") == matching[0].get("callId") and event.get("isError") is False
            for event in tool_ends
        ):
            reasons.append("slow_delivery_failed")
    elif tool_starts:
        reasons.append("unexpected_tool")
    if any(event.get("type") == "error" for event in events):
        reasons.append("sidecar_error")
    return {
        "name": case.name,
        "status": "passed" if not reasons else "failed",
        "failure_codes": sorted(set(reasons)),
        "response_detail": case.response_detail,
        "tool_start_count": len(tool_starts),
    }


def main() -> None:
    options = arguments()
    sidecar_url = options.sidecar_url.rstrip("/")
    control_url = options.control_url.rstrip("/")
    bootstrap_token = os.environ.get("RADCLIFFE_BOOTSTRAP_TOKEN")
    internal_token = os.environ.get("RADCLIFFE_INTERNAL_TOKEN")
    if not bootstrap_token or not internal_token:
        raise SystemExit("the protected bootstrap and internal tokens are required")
    expected_provider = "ollama" if options.profile == "qwen" else "anthropic"
    expected_model = "qwen3:8b" if options.profile == "qwen" else "claude-haiku-4-5-20251001"
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    results: list[dict[str, object]] = []
    cleanup = {"session_ended": False, "device_revoked": False}
    with httpx.Client(timeout=120.0) as client:
        health = client.get(f"{sidecar_url}/healthz")
        health.raise_for_status()
        diagnostic = health.json()
        if (
            diagnostic.get("provider") != expected_provider
            or diagnostic.get("model_id") != expected_model
        ):
            raise SystemExit("sidecar profile does not match the requested corpus profile")
        enrollment_nonce = secrets.token_urlsafe(32)
        credential = f"rdc_dev_{secrets.token_urlsafe(32)}"
        enrolled = client.post(
            f"{control_url}/v1/devices/enroll",
            headers={"Authorization": f"Bearer {bootstrap_token}"},
            json={
                "installation_id": f"exact-output-{enrollment_nonce}",
                "display_name": "Exact output conformance",
                "credential": credential,
            },
        )
        enrolled.raise_for_status()
        device_id = str(enrolled.json()["device_id"])
        device_headers = {"Authorization": f"Bearer {credential}"}
        created = client.post(
            f"{control_url}/v1/sessions",
            headers=device_headers,
            json={
                "participant_identity": "exact-output-conformance",
                "idempotency_key": f"exact-output-{enrollment_nonce}",
                "title": "Exact output conformance",
            },
        )
        created.raise_for_status()
        session_id = str(created.json()["session_id"])
        try:
            for case in CASES:
                events: list[dict[str, Any]] = []
                with client.stream(
                    "POST",
                    f"{sidecar_url}/v1/turns",
                    json=turn_request(case, session_id),
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        events.append(event)
                        if event.get("type") == "usage":
                            usage["input_tokens"] += int(event.get("inputTokens", 0))
                            usage["output_tokens"] += int(event.get("outputTokens", 0))
                            usage["cache_read_tokens"] += int(event.get("cacheReadTokens", 0))
                            usage["cache_write_tokens"] += int(event.get("cacheWriteTokens", 0))
                            usage["estimated_cost_usd"] += float(event.get("estimatedCostUsd", 0.0))
                results.append(validate_case(case, events))
        finally:
            ended = client.patch(
                f"{control_url}/v1/sessions/{session_id}",
                headers=device_headers,
                json={"status": "ended"},
            )
            cleanup["session_ended"] = ended.status_code == 200
            revoked = client.post(
                f"{control_url}/v1/devices/{device_id}/revoke",
                headers={"Authorization": f"Bearer {internal_token}"},
            )
            cleanup["device_revoked"] = revoked.status_code == 200
    passed = all(result["status"] == "passed" for result in results)
    report = {
        "schema": "radcliffe-exact-output-conformance-v1",
        "occurred_at": datetime.now(UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "provider": expected_provider,
        "model_id": expected_model,
        "build_revision": diagnostic.get("build_revision"),
        "prompt_or_response_stored": False,
        "cases": results,
        "cleanup": cleanup,
        "usage": usage,
    }
    write_report(options.report, report)
    if not passed:
        raise SystemExit(f"exact output corpus failed; report: {options.report}")
    print(f"Exact output corpus passed for {options.profile}; report: {options.report}")


if __name__ == "__main__":
    main()
