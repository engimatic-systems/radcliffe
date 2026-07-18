from dataclasses import dataclass

import pytest
from radcliffe_media.livekit_adapter import RadcliffeLiveKitTransport


@dataclass
class FakeParticipant:
    sid: str
    identity: str


class FakeRoom:
    def __init__(self, participant: FakeParticipant) -> None:
        self.remote_participants = {participant.identity: participant}


class FakeClient:
    def __init__(self, participant: FakeParticipant) -> None:
        self._room = FakeRoom(participant)
        self._connected = True
        self.sent: list[tuple[bytes, str]] = []

    @property
    def room(self) -> FakeRoom:
        return self._room

    async def send_data(self, data: bytes, participant_id: str) -> None:
        self.sent.append((data, participant_id))


@pytest.mark.asyncio
async def test_targeted_protocol_packet_resolves_participant_sid_to_livekit_identity() -> None:
    participant = FakeParticipant("PA_owner", "radcliffe-dev_owner")
    client = FakeClient(participant)
    transport = object.__new__(RadcliffeLiveKitTransport)
    transport._client = client  # type: ignore[assignment]

    await transport.send_protocol(b"ready", participant.sid)

    assert transport.participant_identity(participant.sid) == participant.identity
    assert client.sent == [(b"ready", participant.identity)]
