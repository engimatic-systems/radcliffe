from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import aiosqlite
import httpx

from radcliffe_control.settings import Settings


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    component: str
    status: Literal["ok", "failed", "disabled"]
    code: str
    observed: int | None = None
    expected: int | None = None


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    status: Literal["ready", "not_ready"]
    checks: tuple[ReadinessCheck, ...]


HttpProbe = Callable[[str, bool], Awaitable[bool]]


def latest_schema_version() -> int:
    versions = (
        int(resource.name.partition("_")[0])
        for resource in files("radcliffe_domain.migrations").iterdir()
        if resource.name.endswith(".sql") and resource.name.partition("_")[0].isdigit()
    )
    return max(versions)


def livekit_health_url(livekit_url: str) -> str | None:
    parsed = urlsplit(livekit_url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme)
    if scheme is None or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, "/", "", ""))


class ReadinessProbe:
    def __init__(
        self,
        settings: Settings,
        *,
        http_probe: HttpProbe | None = None,
    ) -> None:
        self.settings = settings
        self.http_probe = http_probe or self._http_probe

    async def check(
        self,
        *,
        worker_supervisor_available: bool,
        worker_maintenance_running: bool,
        job_executor_available: bool = False,
        job_executor_running: bool = False,
    ) -> ReadinessReport:
        database, sidecar, livekit = await asyncio.gather(
            self._database(),
            self._sidecar(),
            self._livekit(),
        )
        source_root = self._source_root()
        workers = self._workers(
            supervisor_available=worker_supervisor_available,
            maintenance_running=worker_maintenance_running,
        )
        job_executor = self._job_executor(
            available=job_executor_available,
            running=job_executor_running,
        )
        checks = (database, sidecar, livekit, source_root, workers, job_executor)
        return ReadinessReport(
            status="ready" if all(check.status != "failed" for check in checks) else "not_ready",
            checks=checks,
        )

    async def _database(self) -> ReadinessCheck:
        expected = latest_schema_version()
        if not self.settings.database_path.is_file():
            return ReadinessCheck("database", "failed", "database_missing", expected=expected)
        try:
            async with aiosqlite.connect(self.settings.database_path, timeout=1) as database:
                await database.execute("PRAGMA query_only = ON")
                integrity = await (await database.execute("PRAGMA quick_check")).fetchone()
                schema = await (
                    await database.execute("SELECT MAX(version) FROM schema_migrations")
                ).fetchone()
        except (aiosqlite.Error, OSError):
            return ReadinessCheck("database", "failed", "database_unavailable", expected=expected)
        if integrity is None or integrity[0] != "ok":
            return ReadinessCheck("database", "failed", "integrity_failed", expected=expected)
        observed = int(schema[0]) if schema is not None and schema[0] is not None else 0
        if observed != expected:
            return ReadinessCheck(
                "database",
                "failed",
                "schema_mismatch",
                observed=observed,
                expected=expected,
            )
        return ReadinessCheck(
            "database",
            "ok",
            "schema_current",
            observed=observed,
            expected=expected,
        )

    async def _sidecar(self) -> ReadinessCheck:
        url = f"{self.settings.sidecar_url.rstrip('/')}/healthz"
        if await self.http_probe(url, True):
            return ReadinessCheck("sidecar", "ok", "sidecar_ready")
        return ReadinessCheck("sidecar", "failed", "sidecar_unavailable")

    async def _livekit(self) -> ReadinessCheck:
        url = livekit_health_url(self.settings.livekit_url)
        if url is None:
            return ReadinessCheck("livekit", "failed", "livekit_url_invalid")
        if await self.http_probe(url, False):
            return ReadinessCheck("livekit", "ok", "signal_reachable")
        return ReadinessCheck("livekit", "failed", "signal_unavailable")

    def _source_root(self) -> ReadinessCheck:
        root = self.settings.source_root
        if root.is_dir() and os.access(root, os.R_OK):
            return ReadinessCheck("source_root", "ok", "source_root_readable")
        return ReadinessCheck("source_root", "failed", "source_root_unavailable")

    def _workers(
        self,
        *,
        supervisor_available: bool,
        maintenance_running: bool,
    ) -> ReadinessCheck:
        if not self.settings.managed_workers_enabled:
            return ReadinessCheck("worker_supervisor", "disabled", "managed_workers_disabled")
        if not supervisor_available:
            return ReadinessCheck("worker_supervisor", "failed", "supervisor_unavailable")
        if not maintenance_running:
            return ReadinessCheck("worker_supervisor", "failed", "maintenance_stopped")
        return ReadinessCheck("worker_supervisor", "ok", "supervisor_running")

    def _job_executor(self, *, available: bool, running: bool) -> ReadinessCheck:
        if not self.settings.job_executor_enabled:
            return ReadinessCheck("job_executor", "disabled", "job_executor_disabled")
        if not available:
            return ReadinessCheck("job_executor", "failed", "job_executor_unavailable")
        if not running:
            return ReadinessCheck("job_executor", "failed", "job_executor_stopped")
        return ReadinessCheck("job_executor", "ok", "job_executor_running")

    @staticmethod
    async def _http_probe(url: str, require_json_status: bool) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(url)
                response.raise_for_status()
            if require_json_status:
                payload = response.json()
                return isinstance(payload, dict) and payload.get("status") == "ok"
            return True
        except (httpx.HTTPError, ValueError):
            return False
