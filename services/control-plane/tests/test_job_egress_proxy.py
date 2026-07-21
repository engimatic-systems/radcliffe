import asyncio

import pytest
from radcliffe_control import job_egress_proxy
from radcliffe_control.job_egress_proxy import (
    ProxyRequestError,
    filtered_headers,
    open_public_connection,
    public_addresses,
    resolve_public,
    split_authority,
)


def test_proxy_accepts_only_uniformly_public_destinations() -> None:
    assert public_addresses(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    for addresses in (
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["100.64.0.1"],
        ["169.254.169.254"],
        ["224.0.0.1"],
        ["::1"],
        ["fc00::1"],
        ["fe80::1"],
        ["93.184.216.34", "192.168.1.1"],
        [],
    ):
        with pytest.raises(ProxyRequestError):
            public_addresses(addresses)


def test_proxy_restricts_ports_and_strips_proxy_credentials() -> None:
    assert split_authority("example.com", 443) == ("example.com", 443)
    assert split_authority("[2606:2800:220:1:248:1893:25c8:1946]:80", 80)[1] == 80
    for authority in (
        "example.com:22",
        "user@example.com:443",
        "example.com:443/path",
        "example.com:443?query",
    ):
        with pytest.raises(ProxyRequestError):
            split_authority(authority, 443)

    filtered = filtered_headers(
        [
            b"Host: attacker.example\r\n",
            b"Proxy-Authorization: private\r\n",
            b"Connection: keep-alive, X-Remove\r\n",
            b"X-Remove: private-hop-value\r\n",
            b"X-Keep: public-value\r\n",
        ],
        host="example.com",
    )
    assert b"Host: example.com" in filtered
    assert b"attacker.example" not in filtered
    assert b"private-hop-value" not in filtered
    assert b"public-value" in filtered
    assert b"Connection: close" in filtered


@pytest.mark.asyncio
async def test_proxy_rejects_dns_rebinding_answers(monkeypatch) -> None:
    class Resolver:
        async def getaddrinfo(self, *_args, **_kwargs):
            return [
                (2, 1, 6, "", ("93.184.216.34", 443)),
                (2, 1, 6, "", ("192.168.1.20", 443)),
            ]

    monkeypatch.setattr(job_egress_proxy.asyncio, "get_running_loop", lambda: Resolver())

    with pytest.raises(ProxyRequestError, match="not public"):
        await resolve_public("rebind.example", 443)


@pytest.mark.asyncio
async def test_proxy_retries_only_validated_public_addresses(monkeypatch) -> None:
    attempts: list[tuple[str, int]] = []

    async def addresses(_host: str, _port: int) -> tuple[str, ...]:
        return "2001:4860:4860::8888", "93.184.216.34"

    async def connect(address: str, port: int):
        attempts.append((address, port))
        if ":" in address:
            raise OSError("IPv6 route unavailable")
        return object(), object()

    monkeypatch.setattr(job_egress_proxy, "resolve_public", addresses)
    monkeypatch.setattr(job_egress_proxy.asyncio, "open_connection", connect)

    reader, writer = await open_public_connection("example.com", 443)

    assert reader is not None and writer is not None
    assert attempts == [("2001:4860:4860::8888", 443), ("93.184.216.34", 443)]


class MemoryWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, value: bytes) -> None:
        self.buffer.extend(value)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


@pytest.mark.asyncio
async def test_proxy_rejects_private_literal_before_connecting() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"CONNECT 169.254.169.254:443 HTTP/1.1\r\nHost: 169.254.169.254\r\n\r\n")
    reader.feed_eof()
    writer = MemoryWriter()

    await job_egress_proxy.handle(reader, writer)  # type: ignore[arg-type]

    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403")
    assert b"destination is not public" in writer.buffer
    assert writer.closed
