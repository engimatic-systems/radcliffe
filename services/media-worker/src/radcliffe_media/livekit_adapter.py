from dataclasses import dataclass

from livekit import rtc
from pipecat.transports.livekit.transport import (
    LiveKitParams,
    LiveKitTransport,
    LiveKitTransportClient,
)


@dataclass(frozen=True, slots=True)
class LiveKitConnection:
    url: str
    token: str
    room_name: str


class RadcliffeLiveKitTransportClient(LiveKitTransportClient):
    async def _async_on_data_received(self, data: rtc.DataPacket) -> None:
        participant_id = data.participant.sid if data.participant is not None else ""
        await self._callbacks.on_data_received(data.data, participant_id)


class RadcliffeLiveKitTransport(LiveKitTransport):
    def __init__(
        self,
        url: str,
        token: str,
        room_name: str,
        params: LiveKitParams | None = None,
        input_name: str | None = None,
        output_name: str | None = None,
    ) -> None:
        super().__init__(url, token, room_name, params, input_name, output_name)
        callbacks = self._client._callbacks
        self._client = RadcliffeLiveKitTransportClient(
            url, token, room_name, self._params, callbacks, self.name
        )

    async def _on_data_received(self, data: bytes, participant_id: str) -> None:
        """Preserve Protocol v2 binary payloads instead of decoding them as UTF-8."""
        await self._call_event_handler("on_data_received", data, participant_id)

    async def send_protocol(self, data: bytes, participant_id: str | None = None) -> None:
        if self._client._room is None or not self._client._connected:
            return
        if participant_id is not None:
            identity = self.participant_identity(participant_id)
            if identity is not None:
                await self._client.send_data(data, identity)
            return
        for participant in tuple(self._client.room.remote_participants.values()):
            await self._client.send_data(data, participant.identity)

    def participant_identity(self, participant_id: str) -> str | None:
        if self._client._room is None:
            return None
        participant = next(
            (
                item
                for item in self._client.room.remote_participants.values()
                if item.sid == participant_id or item.identity == participant_id
            ),
            None,
        )
        return participant.identity if participant is not None else None


def create_transport(connection: LiveKitConnection) -> RadcliffeLiveKitTransport:
    """Create the replaceable Pipecat transport used by the production runner."""
    return RadcliffeLiveKitTransport(
        url=connection.url,
        token=connection.token,
        room_name=connection.room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            audio_out_channels=1,
        ),
        input_name="radcliffe-livekit-input",
        output_name="radcliffe-livekit-output",
    )
