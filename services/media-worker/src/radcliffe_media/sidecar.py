from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SidecarEvent:
    type: str
    payload: dict[str, object]


class SidecarClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=5.0)
        self.transport = transport

    async def turn(
        self, request: dict[str, object], cancelled: object | None = None
    ) -> AsyncIterator[SidecarEvent]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for attempt in range(3):
                async with client.stream(
                    "POST", f"{self.base_url}/v1/turns", json=request
                ) as response:
                    if response.status_code == 503 and attempt < 2:
                        await response.aread()
                        await asyncio.sleep(0.25 * 2**attempt)
                        continue
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if isinstance(cancelled, asyncio.Event) and cancelled.is_set():
                            return
                        if not line:
                            continue
                        payload: dict[str, object] = json.loads(line)
                        event_type = str(payload.pop("type"))
                        yield SidecarEvent(event_type, payload)
                    return
