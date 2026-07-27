#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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
    expected_tool: str
    expected_rate: str
    expected_scope: str | None = None
    expected_path: str | None = None


CASES = (
    Case("slow_count", "Count slowly from 1 to 6.", "configure", "0.75x", "turn"),
    Case(
        "faster_answer",
        "Answer faster for this answer: what is two plus two?",
        "configure",
        "1.25x",
        "turn",
    ),
    Case("slow_session", "Speak at 0.75x for this session.", "configure", "0.75x", "session"),
    Case(
        "default_session",
        "Use the default speech rate for this session.",
        "configure",
        "1.0x",
        "session",
    ),
    Case(
        "normal_turn",
        "Reply at a normal speech rate for this turn: say ready.",
        "configure",
        "1.0x",
        "turn",
    ),
    Case(
        "named_source",
        "Read the authorized source at exact path radcliffe-demo.md slowly.",
        "read_source",
        "0.75x",
        expected_path="radcliffe-demo.md",
    ),
)

RATE_ALIASES = {
    "slow": "0.75x",
    "slowly": "0.75x",
    "normal": "1.0x",
    "fast": "1.25x",
    "faster": "1.25x",
    "very_fast": "1.5x",
    0.75: "0.75x",
    1.0: "1.0x",
    1.25: "1.25x",
    1.5: "1.5x",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the content-free delivery-speed routing corpus against one sidecar."
    )
    parser.add_argument("--sidecar-url", default="http://127.0.0.1:8787")
    parser.add_argument("--control-url", default="http://127.0.0.1:8000")
    parser.add_argument("--profile", choices=("qwen", "sonnet"), required=True)
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
        "requestId": f"delivery-{case.name}-{nonce}",
        "sessionId": session_id,
        "generation": 1,
        "operationId": f"delivery-operation-{nonce}",
        "cancellationId": f"delivery-cancellation-{nonce}",
        "traceId": "trc_" + secrets.token_hex(16),
        "responseDetail": "brief",
        "packets": [
            {
                "projectionVersion": "context-v2",
                "messageId": f"delivery-message-{nonce}",
                "role": "user",
                "content": case.prompt,
                "source": "user",
                "annotations": [["radcliffe.turn_id", f"delivery-turn-{nonce}"]],
                "include": True,
                "timestamp": int(datetime.now(UTC).timestamp() * 1000),
            }
        ],
    }


def canonical_rate(value: object) -> object:
    return RATE_ALIASES.get(value, value)


def validate_case(case: Case, events: list[dict[str, Any]]) -> dict[str, object]:
    starts = [event for event in events if event.get("type") == "tool_start"]
    ends = [event for event in events if event.get("type") == "tool_end"]
    errors = [event for event in events if event.get("type") == "error"]
    completed = any(event.get("type") == "complete" for event in events)
    model_completed = any(event.get("type") == "model_complete" for event in events)
    matching = [event for event in starts if event.get("toolName") == case.expected_tool]
    forbidden = "read_source" if case.expected_tool == "configure" else "configure"
    reasons: list[str] = []
    if len(matching) != 1:
        reasons.append("expected_tool_count")
    if any(event.get("toolName") == forbidden for event in starts):
        reasons.append("forbidden_tool")
    if errors or not completed or not model_completed:
        reasons.append("turn_incomplete")
    if matching:
        parameters = matching[0].get("arguments")
        if not isinstance(parameters, dict):
            reasons.append("missing_arguments")
        else:
            if (
                canonical_rate(parameters.get("value", parameters.get("rate")))
                != case.expected_rate
            ):
                reasons.append("rate")
            if case.expected_scope is not None and parameters.get("scope") != case.expected_scope:
                reasons.append("scope")
            if case.expected_path is not None and parameters.get("path") != case.expected_path:
                reasons.append("path")
        call_id = matching[0].get("callId")
        matching_end = [event for event in ends if event.get("callId") == call_id]
        if len(matching_end) != 1 or matching_end[0].get("isError") is not False:
            reasons.append("tool_failed")
    return {
        "name": case.name,
        "status": "passed" if not reasons else "failed",
        "failure_codes": sorted(set(reasons)),
        "tool_start_count": len(starts),
        "tool_end_count": len(ends),
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
    expected_model = "qwen3:8b" if options.profile == "qwen" else "claude-sonnet-5"
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
                "installation_id": f"delivery-routing-{enrollment_nonce}",
                "display_name": "Delivery routing conformance",
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
                "participant_identity": "delivery-routing-conformance",
                "idempotency_key": f"delivery-routing-{enrollment_nonce}",
                "title": "Delivery routing conformance",
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
        "schema": "radcliffe-delivery-routing-conformance-v1",
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
        raise SystemExit(f"delivery routing corpus failed; report: {options.report}")
    print(f"Delivery routing corpus passed for {options.profile}; report: {options.report}")


if __name__ == "__main__":
    main()
