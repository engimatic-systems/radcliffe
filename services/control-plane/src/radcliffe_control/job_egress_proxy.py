from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import urllib.parse
from collections.abc import Iterable

HEADER_LIMIT = 16 * 1024
TRANSFER_LIMIT = 128 * 1024 * 1024
CONNECTION_LIMIT = 8
CONNECTION_TIMEOUT_SECONDS = 180
UPSTREAM_CONNECT_TIMEOUT_SECONDS = 10
ALLOWED_METHODS = frozenset({"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
HEADER_NAME = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class ProxyRequestError(ValueError):
    pass


def public_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    parsed = tuple(ipaddress.ip_address(value) for value in addresses)
    if not parsed or any(not address.is_global or address.is_multicast for address in parsed):
        raise ProxyRequestError("destination is not public")
    return tuple(dict.fromkeys(str(address) for address in parsed))


def split_authority(authority: str, default_port: int) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(f"//{authority}")
    try:
        port = parsed.port or default_port
    except ValueError as error:
        raise ProxyRequestError("invalid destination port") from error
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port not in {80, 443}
    ):
        raise ProxyRequestError("destination port is not allowed")
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ProxyRequestError("invalid destination host") from error
    return host, port


async def resolve_public(host: str, port: int) -> tuple[str, ...]:
    if host.lower() in {"localhost", "host.docker.internal", "host.lima.internal"}:
        raise ProxyRequestError("destination is not public")
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return public_addresses(str(record[4][0]) for record in records)


async def open_public_connection(
    host: str,
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    last_error: OSError | TimeoutError | None = None
    for address in await resolve_public(host, port):
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port),
                timeout=UPSTREAM_CONNECT_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError) as error:
            last_error = error
    raise OSError("public destination is unavailable") from last_error


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


def filtered_headers(lines: list[bytes], *, host: str | None = None) -> bytes:
    blocked = {b"connection", b"proxy-authorization", b"proxy-connection"}
    parsed: list[tuple[bytes, bytes]] = []
    for line in lines:
        key, separator, value = line.partition(b":")
        key = key.strip().lower()
        if not separator or not HEADER_NAME.fullmatch(key):
            raise ProxyRequestError("invalid request header")
        parsed.append((key, value))
        if key == b"connection":
            blocked.update(token.strip().lower() for token in value.split(b",") if token.strip())

    kept = []
    for line, (key, _value) in zip(lines, parsed, strict=True):
        if key not in blocked and (host is None or key != b"host"):
            kept.append(line)
    if host is not None:
        kept.append(f"Host: {host}\r\n".encode("ascii"))
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
    upstream_reader, upstream_writer = await open_public_connection(host, port)
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
    upstream_reader, upstream_writer = await open_public_connection(host, port)
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    host_header = f"[{host}]" if ":" in host else host
    upstream_writer.write(
        f"{method} {path} {version}\r\n".encode()
        + filtered_headers(headers, host=host_header)
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
    if writer.is_closing():
        return
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


async def bounded_handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        try:
            await asyncio.wait_for(handle(reader, writer), timeout=CONNECTION_TIMEOUT_SECONDS)
        except TimeoutError:
            await reject(writer, 408, "proxy connection exceeded its time limit")


async def serve() -> None:
    semaphore = asyncio.Semaphore(CONNECTION_LIMIT)

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await bounded_handle(reader, writer, semaphore)

    server = await asyncio.start_server(accept, "0.0.0.0", 8080, limit=HEADER_LIMIT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
