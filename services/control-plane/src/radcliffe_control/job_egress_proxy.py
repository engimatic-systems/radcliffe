from __future__ import annotations

import asyncio
import ipaddress
import urllib.parse
from collections.abc import Iterable

HEADER_LIMIT = 16 * 1024
TRANSFER_LIMIT = 128 * 1024 * 1024
ALLOWED_METHODS = frozenset({"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})


class ProxyRequestError(ValueError):
    pass


def public_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    parsed = tuple(ipaddress.ip_address(value) for value in addresses)
    if not parsed or any(not address.is_global for address in parsed):
        raise ProxyRequestError("destination is not public")
    return tuple(str(address) for address in parsed)


def split_authority(authority: str, default_port: int) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(f"//{authority}")
    try:
        port = parsed.port or default_port
    except ValueError as error:
        raise ProxyRequestError("invalid destination port") from error
    if not parsed.hostname or port not in {80, 443}:
        raise ProxyRequestError("destination port is not allowed")
    return parsed.hostname.rstrip("."), port


async def resolve_public(host: str, port: int) -> tuple[str, int]:
    if host.lower() in {"localhost", "host.docker.internal", "host.lima.internal"}:
        raise ProxyRequestError("destination is not public")
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=0, proto=0)
    addresses = public_addresses(str(record[4][0]) for record in records)
    return addresses[0], port


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    transferred = 0
    try:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                return
            transferred += len(chunk)
            if transferred > TRANSFER_LIMIT:
                return
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        return
    finally:
        writer.close()


def filtered_headers(lines: list[bytes]) -> bytes:
    blocked = {b"connection", b"proxy-authorization", b"proxy-connection"}
    kept = []
    for line in lines:
        key = line.partition(b":")[0].strip().lower()
        if key not in blocked:
            kept.append(line)
    kept.append(b"Connection: close\r\n")
    return b"".join(kept)


async def handle_connect(
    authority: str,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    host, port = split_authority(authority, 443)
    if port != 443:
        raise ProxyRequestError("CONNECT is restricted to HTTPS")
    address, port = await resolve_public(host, port)
    upstream_reader, upstream_writer = await asyncio.open_connection(address, port)
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(
        relay(reader, upstream_writer),
        relay(upstream_reader, writer),
        return_exceptions=True,
    )


async def handle_http(
    method: str,
    target: str,
    version: str,
    headers: list[bytes],
    initial_body: bytes,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme != "http" or not parsed.netloc:
        raise ProxyRequestError("plain HTTP proxy requests require an absolute URL")
    host, port = split_authority(parsed.netloc, 80)
    if port != 80:
        raise ProxyRequestError("plain HTTP is restricted to port 80")
    address, port = await resolve_public(host, port)
    upstream_reader, upstream_writer = await asyncio.open_connection(address, port)
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    upstream_writer.write(
        f"{method} {path} {version}\r\n".encode()
        + filtered_headers(headers)
        + b"\r\n"
        + initial_body
    )
    await upstream_writer.drain()
    await asyncio.gather(
        relay(reader, upstream_writer),
        relay(upstream_reader, writer),
        return_exceptions=True,
    )


async def reject(writer: asyncio.StreamWriter, status: int, detail: str) -> None:
    body = detail.encode()[:256]
    writer.write(
        (
            f"HTTP/1.1 {status} Rejected\r\nConnection: close\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode()
        + body
    )
    await writer.drain()
    writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        if len(raw) > HEADER_LIMIT:
            raise ProxyRequestError("request headers are too large")
        header, initial_body = raw.split(b"\r\n\r\n", 1)
        lines = header.splitlines(keepends=True)
        request_line = lines.pop(0).decode("ascii").strip().split()
        if len(request_line) != 3:
            raise ProxyRequestError("invalid request line")
        method, target, version = request_line
        if method not in ALLOWED_METHODS or version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise ProxyRequestError("request method or version is not allowed")
        normalized_headers = [line.rstrip(b"\r\n") + b"\r\n" for line in lines]
        if method == "CONNECT":
            await handle_connect(target, reader, writer)
        else:
            await handle_http(
                method,
                target,
                version,
                normalized_headers,
                initial_body,
                reader,
                writer,
            )
    except (
        OSError,
        TimeoutError,
        UnicodeError,
        ValueError,
        asyncio.LimitOverrunError,
        ProxyRequestError,
    ) as error:
        await reject(writer, 403, str(error) or "request rejected")


async def serve() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", 8080, limit=HEADER_LIMIT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
