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
    category: str | None = None
    marker: str | None = None
    exact: bool = False
    source: bool = False


CASES = (
    Case(
        "hash",
        "Give a one-sentence status and include checksum "
        "aabbccddeeff00112233445566778899 for the visual record.",
        "hash",
        "aabbccddeeff00112233445566778899",
    ),
    Case(
        "job_identifier",
        "Give a one-sentence status and include job_0123456789abcdef for the visual record.",
        "identifier",
        "job_0123456789abcdef",
    ),
    Case(
        "uuid",
        "Explain that the item is ready and include 123e4567-e89b-12d3-a456-426614174000 visually.",
        "identifier",
        "123e4567-e89b-12d3-a456-426614174000",
    ),
    Case(
        "url",
        "Give a concise pointer and include https://docs.example.test/guides/start?q=1 visually.",
        "url",
        "https://docs.example.test/guides/start?q=1",
    ),
    Case(
        "path",
        "Say the report is ready and include /srv/radcliffe/output/report.json visually.",
        "path",
        "/srv/radcliffe/output/report.json",
    ),
    Case(
        "timestamp",
        "Say when it completed and include 2026-07-21T04:00:00Z visually.",
        "timestamp",
        "2026-07-21T04:00:00Z",
    ),
    Case(
        "stack_trace",
        "Briefly report a failure and put this frame on its own line for visual debugging: "
        "at worker (/srv/app.ts:12:4)",
        "stack_trace",
        "at worker (/srv/app.ts:12:4)",
    ),
    Case(
        "serialized_data",
        'Say the payload is ready and include {"ready":true,"count":2} visually.',
        "serialized_data",
        '{"ready":true,"count":2}',
    ),
    Case(
        "long_number",
        "Summarize the counter and include 123456789012345 visually.",
        "long_number",
        "123456789012345",
    ),
    Case(
        "code",
        "Say the check is available and include `npm run check` visually.",
        "code",
        "`npm run check`",
    ),
    Case(
        "citation",
        "State that the result is supported and include citation [12] visually.",
        "citation",
        "[12]",
    ),
    Case(
        "repeated_punctuation",
        "Celebrate completion in one sentence and include !!!!! in the visual text.",
        "repeated_punctuation",
        "!!!!!",
    ),
    Case(
        "acronym_metadata",
        "Say metadata is available and include HTTP UUID SHA256 JSON READY visually.",
        "acronym_metadata",
        "HTTP UUID SHA256 JSON READY",
    ),
    Case(
        "exact_hash",
        "Repeat this hash exactly: aabbccddeeff00112233445566778899",
        marker="aabbccddeeff00112233445566778899",
        exact=True,
    ),
    Case(
        "exact_url",
        "Repeat exactly https://docs.example.test/guides/start?q=1",
        marker="https://docs.example.test/guides/start?q=1",
        exact=True,
    ),
    Case(
        "canonical_source",
        "Read the authorized source at exact path radcliffe-demo.md.",
        source=True,
    ),
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run content-free spoken-output evaluation against one sidecar."
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
        "requestId": f"spoken-{case.name}-{nonce}",
        "sessionId": session_id,
        "generation": 1,
        "operationId": f"spoken-operation-{nonce}",
        "cancellationId": f"spoken-cancellation-{nonce}",
        "traceId": "trc_" + secrets.token_hex(16),
        "responseDetail": "brief",
        "packets": [
            {
                "projectionVersion": "context-v2",
                "messageId": f"spoken-message-{nonce}",
                "role": "user",
                "content": case.prompt,
                "source": "user",
                "annotations": [["radcliffe.turn_id", f"spoken-turn-{nonce}"]],
                "include": True,
                "timestamp": int(datetime.now(UTC).timestamp() * 1000),
            }
        ],
    }


def validate_case(case: Case, events: list[dict[str, Any]]) -> dict[str, object]:
    reasons: list[str] = []
    complete = next((event for event in events if event.get("type") == "model_complete"), None)
    completed = any(event.get("type") == "complete" for event in events)
    display = str(complete.get("text", "")) if complete else ""
    deltas = [event for event in events if event.get("type") == "model_delta"]
    spoken = "".join(str(event.get("spokenText", event.get("text", ""))) for event in deltas)
    categories = sorted(
        {
            str(category)
            for event in deltas
            for category in event.get("spokenCategories", [])
            if isinstance(category, str)
        }
    )
    if not completed or complete is None or any(event.get("type") == "error" for event in events):
        reasons.append("turn_incomplete")
    display_recurrence = bool(case.marker and case.marker in display)
    spoken_recurrence = bool(case.marker and case.marker in spoken)
    if case.exact:
        if display != case.marker or spoken != case.marker or categories:
            reasons.append("exact_changed")
    elif case.source:
        starts = [event for event in events if event.get("type") == "tool_start"]
        reads = [event for event in starts if event.get("toolName") == "read_source"]
        ends = [event for event in events if event.get("type") == "tool_end"]
        if len(reads) != 1 or not any(
            event.get("callId") == reads[0].get("callId") and event.get("isError") is False
            for event in ends
        ):
            reasons.append("canonical_source")
    else:
        if spoken_recurrence:
            reasons.append("machine_detail_spoken")
        if display_recurrence and not categories:
            reasons.append("undetected_display_detail")
    return {
        "name": case.name,
        "mode": "exact" if case.exact else "source" if case.source else "ordinary",
        "status": "passed" if not reasons else "failed",
        "failure_codes": sorted(set(reasons)),
        "display_recurrence": display_recurrence,
        "spoken_recurrence": spoken_recurrence,
        "detected_categories": categories,
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
                "installation_id": f"spoken-output-{enrollment_nonce}",
                "display_name": "Spoken output conformance",
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
                "participant_identity": "spoken-output-conformance",
                "idempotency_key": f"spoken-output-{enrollment_nonce}",
                "title": "Spoken output conformance",
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
        "schema": "radcliffe-spoken-output-conformance-v1",
        "occurred_at": datetime.now(UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "provider": expected_provider,
        "model_id": expected_model,
        "build_revision": diagnostic.get("build_revision"),
        "prompt_or_response_stored": False,
        "cases": results,
        "ordinary_display_recurrence_count": sum(
            result["mode"] == "ordinary" and bool(result["display_recurrence"])
            for result in results
        ),
        "ordinary_spoken_recurrence_count": sum(
            result["mode"] == "ordinary" and bool(result["spoken_recurrence"]) for result in results
        ),
        "exact_preservation_count": sum(
            result["mode"] == "exact" and bool(result["spoken_recurrence"]) for result in results
        ),
        "cleanup": cleanup,
        "usage": usage,
    }
    write_report(options.report, report)
    if not passed:
        raise SystemExit(f"spoken output corpus failed; report: {options.report}")
    print(f"Spoken output corpus passed for {options.profile}; report: {options.report}")


if __name__ == "__main__":
    main()
