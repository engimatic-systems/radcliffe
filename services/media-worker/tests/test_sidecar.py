import httpx
import pytest
from radcliffe_media.sidecar import SidecarClient


@pytest.mark.asyncio
async def test_turn_retries_a_transient_pre_stream_service_failure() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": "context accounting unavailable"})
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=(b'{"type":"model_complete","text":"Recovered."}\n{"type":"complete"}\n'),
        )

    client = SidecarClient(
        "http://sidecar.test",
        transport=httpx.MockTransport(handler),
    )

    events = [event async for event in client.turn({"requestId": "request-1"})]

    assert attempts == 2
    assert [(event.type, event.payload) for event in events] == [
        ("model_complete", {"text": "Recovered."}),
        ("complete", {}),
    ]
