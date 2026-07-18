from __future__ import annotations

import base64

import httpx
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


class SessionActionClient:
    def __init__(self, sidecar_url: str, timeout_seconds: float = 10.0) -> None:
        self.sidecar_url = sidecar_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=5.0)

    async def confirm(
        self,
        confirmation: protocol_pb2.AgentControlConfirmation,
        *,
        participant_identity: str,
    ) -> dict[str, object]:
        payload = {
            "confirmation_proto_base64": base64.b64encode(
                confirmation.SerializeToString(deterministic=True)
            ).decode("ascii"),
            "participant_identity": participant_identity,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.sidecar_url}/v1/agent-control-confirmations",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("details"), dict):
            raise RuntimeError("session action confirmation returned an invalid result")
        return result
