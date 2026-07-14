from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import httpx


class ContextUsageClientError(RuntimeError):
    pass


class ContextUsageClient(Protocol):
    async def measure(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


class SidecarContextUsageClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def measure(self, request: Mapping[str, object]) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/v1/context-usage",
                    json=dict(request),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ContextUsageClientError("context accounting sidecar is unavailable") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ContextUsageClientError(
                "context accounting sidecar returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ContextUsageClientError("context accounting sidecar returned an invalid response")
        return payload
