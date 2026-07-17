from __future__ import annotations

import secrets
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True, slots=True)
class EnrolledDevice:
    device_id: str
    participant_identity: str
    credential: str = field(repr=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential}"}


async def enroll_device(
    client: httpx.AsyncClient,
    *,
    control_url: str,
    bootstrap_token: str,
    installation_id: str,
    display_name: str,
    credential: str | None = None,
) -> EnrolledDevice:
    raw_credential = credential or f"rdc_dev_{secrets.token_urlsafe(32)}"
    response = await client.post(
        f"{control_url.rstrip('/')}/v1/devices/enroll",
        headers={"Authorization": f"Bearer {bootstrap_token}"},
        json={
            "installation_id": installation_id,
            "display_name": display_name,
            "credential": raw_credential,
        },
    )
    response.raise_for_status()
    body = response.json()
    return EnrolledDevice(
        device_id=str(body["device_id"]),
        participant_identity=str(body["participant_identity"]),
        credential=raw_credential,
    )
