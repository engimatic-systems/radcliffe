#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from device_auth import enroll_device
from radcliffe_domain.envelope import MessageEnvelope
from radcliffe_protocol.radcliffe.v2.protocol_pb2 import MessageEnvelope as ProtoEnvelope

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_TOKEN = "session-conformance-bootstrap"
INTERNAL_TOKEN = "session-conformance-internal"
TOOL_CORRELATION = {
    "operationId": "operation-session-conformance",
    "traceId": "trc_" + "2" * 32,
}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str
    measured: dict[str, object] = field(default_factory=dict)


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if str(key).casefold()
                in {
                    "participant_token",
                    "sourceenvelopeprotobase64",
                    "livekit_url",
                    "locator",
                }
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class IsolatedControlPlane:
    def __init__(self, *, port: int, environment: dict[str, str], output: Path) -> None:
        self.port = port
        self.environment = environment
        self.output = output
        self.process: asyncio.subprocess.Process | None = None
        self.log_file: Any = None
        self.run_number = 0

    async def start(self, client: httpx.AsyncClient) -> None:
        self.run_number += 1
        log_path = self.output / f"control-{self.run_number}.log"
        self.log_file = await asyncio.to_thread(log_path.open, "wb")
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "radcliffe_control.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
            cwd=ROOT,
            env=self.environment,
            stdout=self.log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.returncode is not None:
                raise RuntimeError(f"isolated control plane exited; inspect {log_path}")
            try:
                response = await client.get(f"{self.url}/healthz")
                if response.status_code == 200 and response.json().get("status") == "ok":
                    return
            except (httpx.HTTPError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.1)
        raise TimeoutError("isolated control plane did not become healthy")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None


class SessionConformance:
    def __init__(self, *, output: Path, seed: int, sidecar_url: str) -> None:
        self.output = output
        self.seed = seed
        self.sidecar_url = sidecar_url.rstrip("/")
        self.started = time.monotonic()
        self.checks: list[Check] = []
        self.timeline: list[dict[str, object]] = []
        self.headers: dict[str, str] = {}
        self.internal_headers = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}

    def check(
        self,
        name: str,
        passed: bool,
        detail: str,
        measured: dict[str, object] | None = None,
    ) -> None:
        self.checks.append(Check(name, passed, detail, measured or {}))

    def require(
        self,
        name: str,
        passed: bool,
        detail: str,
        measured: dict[str, object] | None = None,
    ) -> None:
        self.check(name, passed, detail, measured)
        if not passed:
            raise AssertionError(f"{name}: {detail}")

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
    ) -> httpx.Response:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=json_body,
            params=params,
        )
        try:
            body: object = _redact(response.json())
        except json.JSONDecodeError:
            body = "<non-json response>"
        self.timeline.append(
            {
                "at_seconds": round(time.monotonic() - self.started, 3),
                "method": method,
                "path": httpx.URL(url).path,
                "status": response.status_code,
                "response": body,
            }
        )
        return response

    async def run(self, client: httpx.AsyncClient, server: IsolatedControlPlane) -> None:
        sidecar = await client.get(f"{self.sidecar_url}/healthz")
        self.require(
            "local_sidecar_ready",
            sidecar.status_code == 200 and sidecar.json().get("status") == "ok",
            f"sidecar returned HTTP {sidecar.status_code}",
        )

        unauthorized = await self.request(client, "GET", f"{server.url}/v1/sessions")
        self.check(
            "catalog_requires_authentication",
            unauthorized.status_code == 401,
            f"unauthenticated catalog returned HTTP {unauthorized.status_code}",
        )
        enrolled = await enroll_device(
            client,
            control_url=server.url,
            bootstrap_token=BOOTSTRAP_TOKEN,
            installation_id=f"session-conformance-{self.seed}",
            display_name="Session conformance client",
        )
        self.headers = enrolled.headers
        bootstrap_catalog = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions",
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        )
        self.check(
            "bootstrap_is_limited_to_device_enrollment",
            bootstrap_catalog.status_code == 401,
            f"bootstrap catalog request returned HTTP {bootstrap_catalog.status_code}",
        )

        sessions: list[dict[str, object]] = []
        for index, title in enumerate(("Morning notes", "Project review", "Evening recap"), 1):
            idempotency_key = f"session-conformance-{self.seed}-{index}"
            response = await self.request(
                client,
                "POST",
                f"{server.url}/v1/sessions",
                headers=self.headers,
                json_body={
                    "participant_identity": f"conformance-client-{index}",
                    "idempotency_key": idempotency_key,
                    "title": title,
                },
            )
            self.require(
                f"session_{index}_created",
                response.status_code == 200,
                f"session creation returned HTTP {response.status_code}",
            )
            body = response.json()
            sessions.append(body)
            self.check(
                f"session_{index}_uses_enrolled_device_identity",
                body["participant_identity"] == enrolled.participant_identity
                and body["participant_identity"] != f"conformance-client-{index}",
                "room credential identity is owned by the enrolled device",
            )
            if index == 1:
                retry = await self.request(
                    client,
                    "POST",
                    f"{server.url}/v1/sessions",
                    headers=self.headers,
                    json_body={
                        "participant_identity": "conformance-retry",
                        "idempotency_key": idempotency_key,
                        "title": "Retry must not replace the title",
                    },
                )
                self.check(
                    "session_creation_is_idempotent",
                    retry.status_code == 200
                    and retry.json()["session_id"] == body["session_id"]
                    and retry.json()["title"] == title,
                    "creation retry returned the original durable session and title",
                )
            await asyncio.sleep(0.01)

        session_ids = [str(item["session_id"]) for item in sessions]
        first_page = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions",
            headers=self.headers,
            params={"limit": 2},
        )
        first_page_body = first_page.json()
        first_page_ids = [str(item["session_id"]) for item in first_page_body["items"]]
        self.require(
            "recent_sessions_are_newest_first",
            first_page.status_code == 200 and first_page_ids == session_ids[:0:-1],
            f"first page order was {first_page_ids}",
        )
        second_page = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions",
            headers=self.headers,
            params={"limit": 2, "cursor": first_page_body["next_cursor"]},
        )
        self.check(
            "recent_session_cursor_is_stable",
            [item["session_id"] for item in second_page.json()["items"]] == [session_ids[0]]
            and second_page.json()["next_cursor"] is None,
            "second page returned the one remaining session without duplication",
        )

        resumed = await self.request(
            client,
            "POST",
            f"{server.url}/v1/sessions/{session_ids[0]}/credentials",
            headers=self.headers,
            json_body={"participant_identity": "conformance-resumed-client"},
        )
        self.require(
            "recent_session_can_be_resumed",
            resumed.status_code == 200
            and resumed.json()["session_id"] == session_ids[0]
            and resumed.json()["participant_token"] != sessions[0]["participant_token"],
            "resume returned fresh least-privilege credentials for the durable session",
        )
        reordered = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions",
            headers=self.headers,
        )
        reordered_ids = [str(item["session_id"]) for item in reordered.json()["items"]]
        self.check(
            "resumed_session_moves_to_recent_front",
            reordered_ids[:3] == [session_ids[0], session_ids[2], session_ids[1]],
            f"activity order was {reordered_ids[:3]}",
        )

        list_call = await self.request(
            client,
            "POST",
            f"{server.url}/v1/tool-calls",
            headers=self.internal_headers,
            json_body={
                "sessionId": session_ids[0],
                "generation": 1,
                **TOOL_CORRELATION,
                "callId": "conformance-list",
                "idempotencyKey": "conformance-list",
                "toolName": "list_sources",
                "arguments": {},
            },
        )
        self.require(
            "authorized_source_catalog_is_discoverable",
            list_call.status_code == 200,
            f"list_sources returned HTTP {list_call.status_code}",
        )
        listed_paths = json.loads(list_call.json()["modelContent"])["paths"]
        self.check(
            "source_catalog_contains_only_relative_markdown_paths",
            listed_paths == ["notes/conformance-source.md"],
            f"source catalog returned {listed_paths!r}",
        )

        denied = await self.request(
            client,
            "POST",
            f"{server.url}/v1/tool-calls",
            headers=self.internal_headers,
            json_body={
                "sessionId": session_ids[0],
                "generation": 1,
                **TOOL_CORRELATION,
                "callId": "conformance-denied",
                "idempotencyKey": "conformance-denied",
                "toolName": "read_source",
                "arguments": {"path": "../outside.md"},
            },
        )
        self.check(
            "path_traversal_is_denied",
            denied.status_code == 403,
            f"traversal attempt returned HTTP {denied.status_code}",
        )

        source_call_body = {
            "sessionId": session_ids[0],
            "generation": 1,
            **TOOL_CORRELATION,
            "callId": "conformance-read",
            "idempotencyKey": "conformance-read",
            "toolName": "read_source",
            "arguments": {"path": "notes/conformance-source.md"},
        }
        source_call = await self.request(
            client,
            "POST",
            f"{server.url}/v1/tool-calls",
            headers=self.internal_headers,
            json_body=source_call_body,
        )
        self.require(
            "denied_tool_call_does_not_poison_next_call",
            source_call.status_code == 200,
            f"valid read after denial returned HTTP {source_call.status_code}",
        )
        duplicate_source_call = await self.request(
            client,
            "POST",
            f"{server.url}/v1/tool-calls",
            headers=self.internal_headers,
            json_body=source_call_body,
        )
        self.check(
            "source_tool_retry_is_idempotent",
            duplicate_source_call.status_code == 200
            and duplicate_source_call.json()["details"]["sourceMessageId"]
            == source_call.json()["details"]["sourceMessageId"],
            "duplicate read returned the original canonical source envelope",
        )

        encoded_source = source_call.json()["details"]["sourceEnvelopeProtoBase64"]
        source_envelope = MessageEnvelope.from_proto(
            ProtoEnvelope.FromString(base64.b64decode(encoded_source))
        )
        canonical = (self.output / "source-root/notes/conformance-source.md").read_bytes()
        self.check(
            "source_envelope_is_byte_exact",
            source_envelope.content.inline_text is not None
            and source_envelope.content.inline_text.encode() == canonical
            and source_envelope.content_sha256 == hashlib.sha256(canonical).digest()
            and source_envelope.provenance is not None
            and source_envelope.provenance.display_name == "conformance-source.md",
            "transferred envelope retains exact bytes, hashes, and visible provenance",
            {"byte_length": len(canonical), "sha256": hashlib.sha256(canonical).hexdigest()},
        )

        messages = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions/{session_ids[0]}/messages",
            headers=self.headers,
        )
        message_items = messages.json()["items"]
        sequences = [int(item["sequence"]) for item in message_items]
        source_items = [item for item in message_items if item["source"] == "text_source"]
        self.check(
            "unheard_source_is_not_persisted",
            sequences == sorted(sequences) and not source_items,
            "direct tool use retained chronological audit without persisting unheard source text",
        )

        usage = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions/{session_ids[0]}/context-usage",
            headers=self.headers,
        )
        usage_body = usage.json()
        accounting_contract = (
            usage_body.get("exact") is True
            and usage_body.get("measurement_basis") == "ollama-openai-prompt_tokens-v1"
        ) or (
            usage_body.get("exact") is False
            and usage_body.get("measurement_basis") == "anthropic-count-tokens-plus-2pct-32-v1"
        )
        self.require(
            "bounded_context_usage_is_available",
            usage.status_code == 200
            and accounting_contract
            and usage_body["projected_input_tokens"] > 0
            and usage_body["projection_version"] == "context-v2",
            f"context endpoint returned HTTP {usage.status_code}",
            {
                "projected_input_tokens": usage_body.get("projected_input_tokens"),
                "percentage": usage_body.get("percentage"),
                "measurement_basis": usage_body.get("measurement_basis"),
            },
        )
        self.check(
            "public_context_usage_does_not_leak_content_or_digest",
            "content" not in usage_body and "request_digest" not in usage_body,
            "public metric contains accounting only",
        )

        order_before_restart = reordered_ids[:3]
        await server.stop()
        await server.start(client)
        restored_device = await self.request(
            client,
            "GET",
            f"{server.url}/v1/devices/me",
            headers=self.headers,
        )
        self.check(
            "device_authentication_survives_control_plane_restart",
            restored_device.status_code == 200
            and restored_device.json()["device_id"] == enrolled.device_id,
            "the same enrolled device authenticated after restart",
        )
        after_restart = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions",
            headers=self.headers,
        )
        after_restart_ids = [str(item["session_id"]) for item in after_restart.json()["items"]]
        self.check(
            "catalog_order_survives_control_plane_restart",
            after_restart_ids[:3] == order_before_restart,
            f"order after restart was {after_restart_ids[:3]}",
        )
        restored_messages = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions/{session_ids[0]}/messages",
            headers=self.headers,
        )
        self.check(
            "conversation_timeline_survives_control_plane_restart",
            restored_messages.json()["items"] == message_items,
            "chronological tool audit and unheard-source exclusion were restored from SQLite",
        )

        restored_credentials = await self.request(
            client,
            "POST",
            f"{server.url}/v1/sessions/{session_ids[0]}/credentials",
            headers=self.headers,
            json_body={"participant_identity": "conformance-after-restart"},
        )
        self.require(
            "session_credentials_refresh_after_restart",
            restored_credentials.status_code == 200
            and restored_credentials.json()["participant_token"]
            != resumed.json()["participant_token"],
            "restarted control plane issued a fresh credential",
        )
        replayed_after_restart = await self.request(
            client,
            "POST",
            f"{server.url}/v1/tool-calls",
            headers=self.internal_headers,
            json_body=source_call_body,
        )
        self.check(
            "tool_idempotency_survives_control_plane_restart",
            replayed_after_restart.status_code == 200
            and replayed_after_restart.json()["details"]["sourceMessageId"]
            == source_call.json()["details"]["sourceMessageId"],
            "durable tool audit replayed the same source after restart",
        )

        archived = await self.request(
            client,
            "PATCH",
            f"{server.url}/v1/sessions/{session_ids[1]}",
            headers=self.headers,
            json_body={"title": "Project review", "status": "archived"},
        )
        visible_after_archive = await self.request(
            client,
            "GET",
            f"{server.url}/v1/sessions",
            headers=self.headers,
        )
        visible_ids = [item["session_id"] for item in visible_after_archive.json()["items"]]
        self.check(
            "archived_session_leaves_recent_home",
            archived.status_code == 200 and session_ids[1] not in visible_ids,
            "archived conversation is durable but no longer clutters recent sessions",
        )


async def run(args: argparse.Namespace) -> int:
    created_at = datetime.now(UTC)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output) if args.output else ROOT / "artifacts/session-conformance" / stamp
    output.mkdir(parents=True, exist_ok=True)
    source_path = output / "source-root/notes/conformance-source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    nonce = "-".join(rng.sample(("amber", "cedar", "lunar", "river", "violet"), 3))
    source_path.write_text(
        "---\nkind: synthetic-conformance\n---\n"
        f"The deterministic session nonce is {nonce}.  Preserve both spaces here.\n"
    )
    database_path = output / "session-conformance.sqlite3"
    environment = os.environ.copy()
    environment.update(
        {
            "RADCLIFFE_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
            "RADCLIFFE_INTERNAL_TOKEN": INTERNAL_TOKEN,
            "RADCLIFFE_OWNER_ID": "session-conformance",
            "RADCLIFFE_DATABASE_PATH": str(database_path),
            "RADCLIFFE_TELEMETRY_PATH": str(output / "telemetry.jsonl"),
            "RADCLIFFE_SOURCE_ROOT": str(source_path.parents[1]),
            "RADCLIFFE_SIDECAR_URL": args.sidecar_url,
            "RADCLIFFE_LIVEKIT_URL": "ws://127.0.0.1:7880",
            "RADCLIFFE_LIVEKIT_API_KEY": "session-test-key",
            "RADCLIFFE_LIVEKIT_API_SECRET": "session-test-secret-long-enough",
            "RADCLIFFE_MANAGED_WORKERS_ENABLED": "false",
        }
    )
    conformance = SessionConformance(output=output, seed=args.seed, sidecar_url=args.sidecar_url)
    server = IsolatedControlPlane(port=_available_port(), environment=environment, output=output)
    error: str | None = None
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            await server.start(client)
            await conformance.run(client, server)
        except Exception:  # noqa: BLE001 - report infrastructure failures as conformance evidence
            error = traceback.format_exc(limit=12)
            conformance.check("suite_completed", False, error)
        finally:
            await server.stop()

    passed = bool(conformance.checks) and all(check.passed for check in conformance.checks)
    report = {
        "schema": "radcliffe-session-conformance-v1",
        "created_at": created_at.isoformat(),
        "commit": args.commit,
        "seed": args.seed,
        "isolated_control_plane": True,
        "cloud_models_enabled": False,
        "passed": passed,
        "checks": [asdict(check) for check in conformance.checks],
        "timeline": conformance.timeline,
        "artifacts": {
            "database": database_path.name,
            "source": str(source_path.relative_to(output)),
            "control_logs": [f"control-{index}.log" for index in range(1, server.run_number + 1)],
        },
        "error": error,
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "passed": passed,
                "checks": len(conformance.checks),
                "failed": [check.name for check in conformance.checks if not check.passed],
            },
            indent=2,
        )
    )
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--seed", type=int, default=20260716)
    parser.add_argument("--sidecar-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--commit",
        default=subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
