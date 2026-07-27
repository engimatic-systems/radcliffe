#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx

MODEL_ID = "claude-sonnet-5"
APPROVAL_PHRASE = "I_APPROVE_ONE_BOUNDED_CLOUD_SMOKE"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one explicitly approved cloud smoke turn.")
    parser.add_argument("--sidecar-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/cloud-paid-smoke/report.json"),
    )
    return parser.parse_args()


def require_explicit_approval() -> None:
    if os.environ.get("RADCLIFFE_ALLOW_CLOUD_MODELS") != "1":
        raise SystemExit("cloud models are not enabled for this process")
    if os.environ.get("RADCLIFFE_APPROVE_PAID_SMOKE") != APPROVAL_PHRASE:
        raise SystemExit("the one-run paid-smoke approval phrase is missing")


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def main() -> None:
    options = arguments()
    require_explicit_approval()
    sidecar_url = options.sidecar_url.rstrip("/")
    request_nonce = secrets.token_hex(12)
    trace_id = "trc_" + secrets.token_hex(16)
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    provider = ""
    model_id = ""
    model_output = ""
    completed = False

    with httpx.Client(timeout=60.0) as client:
        health = client.get(f"{sidecar_url}/healthz")
        health.raise_for_status()
        diagnostic = health.json()
        if diagnostic.get("provider") != "anthropic" or diagnostic.get("model_id") != MODEL_ID:
            raise SystemExit("sidecar is not running the pinned Anthropic live profile")
        request = {
            "requestId": f"cloud-smoke-{request_nonce}",
            "sessionId": f"cloud-smoke-{request_nonce}",
            "generation": 1,
            "operationId": f"cloud-smoke-operation-{request_nonce}",
            "cancellationId": f"cloud-smoke-cancellation-{request_nonce}",
            "traceId": trace_id,
            "responseDetail": "brief",
            "packets": [
                {
                    "projectionVersion": "context-v2",
                    "messageId": f"cloud-smoke-message-{request_nonce}",
                    "role": "user",
                    "content": "Reply with exactly: Radcliffe cloud smoke passed.",
                    "source": "user",
                    "annotations": [["radcliffe.turn_id", f"cloud-smoke-turn-{request_nonce}"]],
                    "include": True,
                    "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                }
            ],
        }
        with client.stream("POST", f"{sidecar_url}/v1/turns", json=request) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event.get("type") == "usage":
                    provider = str(event.get("provider", ""))
                    model_id = str(event.get("modelId", ""))
                    usage["input_tokens"] += int(event.get("inputTokens", 0))
                    usage["output_tokens"] += int(event.get("outputTokens", 0))
                    usage["cache_read_tokens"] += int(event.get("cacheReadTokens", 0))
                    usage["cache_write_tokens"] += int(event.get("cacheWriteTokens", 0))
                    usage["estimated_cost_usd"] += float(event.get("estimatedCostUsd", 0.0))
                elif event.get("type") == "model_complete":
                    model_output = str(event.get("text", ""))
                elif event.get("type") == "complete":
                    completed = True
                elif event.get("type") == "error":
                    raise SystemExit("the paid smoke returned a sidecar error")

    if (
        not completed
        or provider != "anthropic"
        or model_id != MODEL_ID
        or model_output.strip() != "Radcliffe cloud smoke passed."
        or usage["input_tokens"] <= 0
        or usage["output_tokens"] <= 0
        or usage["estimated_cost_usd"] <= 0
    ):
        raise SystemExit("the paid smoke did not return pinned-provider usage")
    report = {
        "schema": "radcliffe-cloud-paid-smoke-v2",
        "occurred_at": datetime.now(UTC).isoformat(),
        "status": "passed",
        "provider": provider,
        "model_id": model_id,
        "usage": usage,
        "foreground_budget_enforcement": diagnostic["foreground_budget_enforcement"],
        "prompt_or_response_stored": False,
    }
    write_report(options.report, report)
    print(
        f"Haiku smoke passed; estimated cost ${usage['estimated_cost_usd']:.6f}; "
        f"report: {options.report}"
    )


if __name__ == "__main__":
    main()
