from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SidecarEvent:
    type: str
    payload: dict[str, object]


class SidecarClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=5.0)

    async def turn(
        self, request: dict[str, object], cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]:
        del cancelled
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.base_url}/v1/turns", json=request) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    payload: dict[str, object] = json.loads(line)
                    event_type = str(payload.pop("type"))
                    yield SidecarEvent(event_type, payload)
