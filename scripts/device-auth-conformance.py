#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import jwt.api_jwt
from jwt.exceptions import ExpiredSignatureError
from livekit import api

ROOT = Path(__file__).resolve().parent.parent


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


def _credential() -> str:
    return f"rdc_dev_{secrets.token_urlsafe(32)}"


def _jwt_payload(token: str) -> dict[str, Any]:
    encoded = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if any(
                    marker in str(key).casefold()
                    for marker in ("token", "credential", "secret", "authorization")
                )
                else _safe(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str) and (
        value.startswith("rdc_dev_") or (value.count(".") == 2 and value.startswith("eyJ"))
    ):
        return "<redacted>"
    return value


class IsolatedControlPlane:
    def __init__(self, *, port: int, environment: dict[str, str], output: Path) -> None:
        self.port = port
        self.environment = environment
        self.output = output
        self.process: asyncio.subprocess.Process | None = None
        self.log_file: Any = None
        self.run_number = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

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


class DeviceAuthConformance:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.checks: list[Check] = []
        self.timeline: list[dict[str, object]] = []

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
    ) -> httpx.Response:
        response = await client.request(method, url, headers=headers, json=json_body)
        try:
            body: object = _safe(response.json())
        except json.JSONDecodeError:
            body = "<non-json response>"
        self.timeline.append(
            {
                "method": method,
                "path": httpx.URL(url).path,
                "status": response.status_code,
                "response": body,
            }
        )
        return response


async def run() -> int:
    commit = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "artifacts" / "device-auth-conformance" / stamp
    output.mkdir(parents=True, exist_ok=True)
    database = output / "events.sqlite3"
    source_root = output / "sources"
    source_root.mkdir()

    bootstrap = f"bootstrap-{secrets.token_urlsafe(32)}"
    internal = f"internal-{secrets.token_urlsafe(32)}"
    livekit_key = f"security-{secrets.token_hex(8)}"
    livekit_secret = secrets.token_urlsafe(48)
    provider_sentinel = f"provider-{secrets.token_urlsafe(32)}"
    first_credential = _credential()
    conflicting_credential = _credential()
    rotated_credential = _credential()
    second_credential = _credential()
    secrets_to_scan = (
        bootstrap,
        internal,
        livekit_key,
        livekit_secret,
        provider_sentinel,
        first_credential,
        conflicting_credential,
        rotated_credential,
        second_credential,
    )

    inherited = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "PATH", "TMPDIR", "VIRTUAL_ENV", "PYTHONPATH", "XDG_CACHE_HOME"}
    }
    environment = {
        **inherited,
        "PYTHONUNBUFFERED": "1",
        "RADCLIFFE_LIVEKIT_URL": "ws://127.0.0.1:7880",
        "RADCLIFFE_LIVEKIT_API_KEY": livekit_key,
        "RADCLIFFE_LIVEKIT_API_SECRET": livekit_secret,
        "RADCLIFFE_BOOTSTRAP_TOKEN": bootstrap,
        "RADCLIFFE_INTERNAL_TOKEN": internal,
        "RADCLIFFE_OWNER_ID": f"security-owner-{uuid4().hex}",
        "RADCLIFFE_DATABASE_PATH": str(database),
        "RADCLIFFE_SOURCE_ROOT": str(source_root),
        "RADCLIFFE_ROOM_TOKEN_TTL_SECONDS": "60",
        "RADCLIFFE_MANAGED_WORKERS_ENABLED": "0",
        "RADCLIFFE_OPENAI_API_KEY": provider_sentinel,
        "RADCLIFFE_ALLOW_CLOUD_MODELS": "0",
    }
    verifier = api.TokenVerifier(livekit_key, livekit_secret, leeway=timedelta(0))
    plane = IsolatedControlPlane(port=_available_port(), environment=environment, output=output)
    suite = DeviceAuthConformance(output)
    bootstrap_headers = {"Authorization": f"Bearer {bootstrap}"}
    internal_headers = {"Authorization": f"Bearer {internal}"}
    first_headers = {"Authorization": f"Bearer {first_credential}"}
    rotated_headers = {"Authorization": f"Bearer {rotated_credential}"}
    second_headers = {"Authorization": f"Bearer {second_credential}"}
    error: BaseException | None = None

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            await plane.start(client)
            unauthorized = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/enroll",
                json_body={
                    "installation_id": "security-installation-primary",
                    "display_name": "Security test device",
                    "credential": first_credential,
                },
            )
            suite.check(
                "enrollment_requires_bootstrap_authority",
                unauthorized.status_code == 401,
                f"unauthorized enrollment returned HTTP {unauthorized.status_code}",
            )
            enrollment_body = {
                "installation_id": "security-installation-primary",
                "display_name": "Security test device",
                "credential": first_credential,
            }
            first = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/enroll",
                headers=bootstrap_headers,
                json_body=enrollment_body,
            )
            suite.require(
                "first_enrollment_succeeds_without_echoing_credential",
                first.status_code == 200 and "credential" not in first.json(),
                f"first enrollment returned HTTP {first.status_code}",
            )
            enrolled = first.json()
            retry = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/enroll",
                headers=bootstrap_headers,
                json_body=enrollment_body,
            )
            suite.check(
                "identical_enrollment_retry_is_idempotent",
                retry.status_code == 200 and retry.json() == enrolled,
                f"identical retry returned HTTP {retry.status_code}",
            )
            conflict = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/enroll",
                headers=bootstrap_headers,
                json_body={**enrollment_body, "credential": conflicting_credential},
            )
            replay = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/enroll",
                headers=bootstrap_headers,
                json_body={
                    **enrollment_body,
                    "installation_id": "security-installation-replay",
                },
            )
            suite.check(
                "conflicting_enrollment_replay_is_rejected",
                conflict.status_code == 409 and replay.status_code == 409,
                (
                    f"installation conflict={conflict.status_code}, "
                    f"credential replay={replay.status_code}"
                ),
            )

            rejected_statuses = []
            for method, path, body in (
                ("GET", "/v1/sessions", None),
                (
                    "POST",
                    "/v1/sessions",
                    {"participant_identity": "bootstrap-cannot-select-identity"},
                ),
                ("GET", "/v1/devices/me", None),
            ):
                response = await suite.request(
                    client,
                    method,
                    f"{plane.url}{path}",
                    headers=bootstrap_headers,
                    json_body=body,
                )
                rejected_statuses.append(response.status_code)
            suite.check(
                "bootstrap_is_rejected_on_ordinary_device_and_session_apis",
                rejected_statuses == [401, 401, 401],
                f"ordinary API statuses={rejected_statuses}",
            )

            created = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/sessions",
                headers=first_headers,
                json_body={
                    "participant_identity": "caller-must-not-control-this",
                    "idempotency_key": "security-session",
                },
            )
            suite.require(
                "enrolled_device_receives_session_token",
                created.status_code == 200,
                f"session creation returned HTTP {created.status_code}",
            )
            session = created.json()
            claims = verifier.verify(session["participant_token"])
            payload = _jwt_payload(session["participant_token"])
            suite.check(
                "room_token_is_server_identity_room_source_and_ttl_scoped",
                claims.identity == enrolled["participant_identity"]
                and claims.identity != "caller-must-not-control-this"
                and claims.video.room == session["session_id"]
                and claims.video.can_publish_sources == ["microphone"]
                and payload["exp"] - payload["nbf"] == 60,
                "verified signed identity, one room, microphone-only publication, and 60s TTL",
                {"ttl_seconds": payload["exp"] - payload["nbf"]},
            )
            refreshed = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/sessions/{session['session_id']}/credentials",
                headers=first_headers,
                json_body={"participant_identity": "still-not-caller-controlled"},
            )
            suite.require(
                "session_token_refresh_returns_a_fresh_scoped_token",
                refreshed.status_code == 200
                and refreshed.json()["participant_token"] != session["participant_token"],
                f"credential refresh returned HTTP {refreshed.status_code}",
            )
            refreshed_payload = _jwt_payload(refreshed.json()["participant_token"])
            suite.check(
                "session_token_refresh_changes_nonce_not_scope",
                refreshed_payload["attributes"]["radcliffe.token_nonce"]
                != payload["attributes"]["radcliffe.token_nonce"]
                and verifier.verify(refreshed.json()["participant_token"]).identity
                == enrolled["participant_identity"]
                and verifier.verify(refreshed.json()["participant_token"]).video.room
                == session["session_id"],
                "refresh changed the nonce while preserving server-owned identity and room scope",
            )

            real_datetime = datetime
            expiry_timestamp = int(refreshed_payload["exp"]) + 1

            class AfterExpiry(real_datetime):
                @classmethod
                def now(cls, tz=None):
                    return real_datetime.fromtimestamp(expiry_timestamp, tz=tz)

            expired_denied = False
            with patch.object(jwt.api_jwt, "datetime", AfterExpiry):
                try:
                    verifier.verify(refreshed.json()["participant_token"])
                except ExpiredSignatureError:
                    expired_denied = True
            suite.check(
                "expired_room_token_is_rejected_by_official_verifier",
                expired_denied,
                "official LiveKit verifier rejected the signed token at exp+1s",
            )

            await plane.stop()
            await plane.start(client)
            after_restart = await suite.request(
                client,
                "GET",
                f"{plane.url}/v1/devices/me",
                headers=first_headers,
            )
            restart_refresh = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/sessions/{session['session_id']}/credentials",
                headers=first_headers,
                json_body={"participant_identity": "restart-cannot-change-identity"},
            )
            suite.check(
                "device_auth_and_token_refresh_survive_control_restart",
                after_restart.status_code == 200 and restart_refresh.status_code == 200,
                f"device={after_restart.status_code}, refresh={restart_refresh.status_code}",
            )

            rotation = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/me/credential",
                headers=first_headers,
                json_body={"credential": rotated_credential},
            )
            old_denied = await suite.request(
                client,
                "GET",
                f"{plane.url}/v1/devices/me",
                headers=first_headers,
            )
            new_accepted = await suite.request(
                client,
                "GET",
                f"{plane.url}/v1/devices/me",
                headers=rotated_headers,
            )
            confirmation = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/me/credential",
                headers=rotated_headers,
                json_body={"credential": rotated_credential},
            )
            suite.check(
                "credential_rotation_is_immediate_and_lost_response_safe",
                rotation.status_code == 200
                and old_denied.status_code == 401
                and new_accepted.status_code == 200
                and confirmation.status_code == 200,
                (
                    f"rotate={rotation.status_code}, old={old_denied.status_code}, "
                    f"new={new_accepted.status_code}, confirm={confirmation.status_code}"
                ),
            )

            second = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/enroll",
                headers=bootstrap_headers,
                json_body={
                    "installation_id": "security-installation-secondary",
                    "display_name": "Second security test device",
                    "credential": second_credential,
                },
            )
            collision = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/me/credential",
                headers=rotated_headers,
                json_body={"credential": second_credential},
            )
            suite.check(
                "rotation_cannot_take_another_device_credential",
                second.status_code == 200
                and collision.status_code == 409
                and (
                    await suite.request(
                        client,
                        "GET",
                        f"{plane.url}/v1/devices/me",
                        headers=rotated_headers,
                    )
                ).status_code
                == 200,
                f"second enrollment={second.status_code}, collision={collision.status_code}",
            )

            await plane.stop()
            await plane.start(client)
            suite.check(
                "rotated_credential_survives_restart",
                (
                    await suite.request(
                        client,
                        "GET",
                        f"{plane.url}/v1/devices/me",
                        headers=rotated_headers,
                    )
                ).status_code
                == 200,
                "replacement credential authenticated after a second control-plane restart",
            )
            revoked = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/devices/{enrolled['device_id']}/revoke",
                headers=internal_headers,
            )
            revoked_list = await suite.request(
                client,
                "GET",
                f"{plane.url}/v1/sessions",
                headers=rotated_headers,
            )
            revoked_refresh = await suite.request(
                client,
                "POST",
                f"{plane.url}/v1/sessions/{session['session_id']}/credentials",
                headers=rotated_headers,
                json_body={"participant_identity": "revoked"},
            )
            suite.check(
                "revocation_immediately_denies_control_and_token_refresh",
                revoked.status_code == 200
                and revoked_list.status_code == 401
                and revoked_refresh.status_code == 401,
                (
                    f"revoke={revoked.status_code}, list={revoked_list.status_code}, "
                    f"refresh={revoked_refresh.status_code}"
                ),
            )
            audit = await suite.request(
                client,
                "GET",
                f"{plane.url}/v1/internal/device-auth-audit",
                headers=internal_headers,
            )
            suite.require(
                "internal_audit_endpoint_is_available_after_restart",
                audit.status_code == 200,
                f"audit endpoint returned HTTP {audit.status_code}",
            )
            audit_text = audit.text
            audit_types = [item["event_type"] for item in audit.json()]
            suite.check(
                "audit_records_security_outcomes_without_credentials",
                {"enrolled", "enrollment_conflict", "authentication_failed", "rotated", "revoked"}
                <= set(audit_types)
                and all(secret not in audit_text for secret in secrets_to_scan),
                f"content-free audit event types={sorted(set(audit_types))}",
                {"event_count": len(audit_types)},
            )
            suite.check(
                "second_device_remains_valid_after_first_device_revocation",
                (
                    await suite.request(
                        client,
                        "GET",
                        f"{plane.url}/v1/devices/me",
                        headers=second_headers,
                    )
                ).status_code
                == 200,
                "revocation remained scoped to the selected device",
            )
        except BaseException as caught:
            error = caught
            suite.check("scenario_execution", False, f"{type(caught).__name__}: {caught}")
        finally:
            await plane.stop()

    artifact_paths = tuple(path for path in output.rglob("*") if path.is_file())
    leaks: list[str] = []
    for path in artifact_paths:
        content = path.read_bytes()
        for index, secret in enumerate(secrets_to_scan):
            if secret.encode() in content:
                leaks.append(f"{path.relative_to(output)}:secret-{index}")
    suite.check(
        "artifacts_contain_no_raw_control_device_livekit_or_provider_secret",
        not leaks,
        "artifact secret scan passed" if not leaks else f"secret leaks: {leaks}",
        {"files_scanned": len(artifact_paths), "secret_classes": len(secrets_to_scan)},
    )

    report = {
        "schema": "radcliffe-device-auth-conformance-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": commit.stdout.strip(),
        "passed": all(check.passed for check in suite.checks) and error is None,
        "checks": [asdict(check) for check in suite.checks],
        "timeline": suite.timeline,
        "artifacts": [str(path.relative_to(output)) for path in artifact_paths],
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    final_artifact_paths = tuple(path for path in output.rglob("*") if path.is_file())
    final_leaks: list[str] = []
    for path in final_artifact_paths:
        content = path.read_bytes()
        for index, secret in enumerate(secrets_to_scan):
            if secret.encode() in content:
                final_leaks.append(f"{path.relative_to(output)}:secret-{index}")
    suite.checks[-1] = Check(
        "artifacts_contain_no_raw_control_device_livekit_or_provider_secret",
        not final_leaks,
        "artifact secret scan passed" if not final_leaks else f"secret leaks: {final_leaks}",
        {
            "files_scanned": len(final_artifact_paths),
            "secret_classes": len(secrets_to_scan),
        },
    )
    report["passed"] = all(check.passed for check in suite.checks) and error is None
    report["checks"] = [asdict(check) for check in suite.checks]
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "passed": report["passed"],
                "checks": len(suite.checks),
                "failed": [check.name for check in suite.checks if not check.passed],
            },
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
