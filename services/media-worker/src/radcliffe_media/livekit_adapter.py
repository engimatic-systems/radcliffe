from dataclasses import dataclass

from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport


@dataclass(frozen=True, slots=True)
class LiveKitConnection:
    url: str
    token: str
    room_name: str


def create_transport(connection: LiveKitConnection) -> LiveKitTransport:
    """Create the replaceable Pipecat transport used by the production runner."""
    return LiveKitTransport(
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
