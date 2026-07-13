import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_domain.envelope import SourceKind
from radcliffe_domain.projection import render_projection
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import (
    CapabilityGrant,
    ToolCancelledError,
    ToolDeniedError,
    ToolGateway,
)


def grant(root, *, generation: int = 1, revoked: bool = False) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant-1",
        session_id="s1",
        generation=generation,
        readable_roots=(root,),
        allowed_tools=frozenset({"read_source"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        revoked=revoked,
    )


@pytest.mark.asyncio
async def test_read_source_is_exact_auditable_and_idempotent(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    fixture = source_root / "demo.md"
    fixture.write_bytes(b"# Canonical\n\nRead me exactly.\n")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    gateway = ToolGateway(store)

    result = await gateway.read_source(
        call_id="call-1",
        idempotency_key="idem-1",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": "demo.md"},
    )
    replay = await gateway.read_source(
        call_id="call-1",
        idempotency_key="idem-1",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": "demo.md"},
    )

    assert result.canonical_bytes == fixture.read_bytes()
    assert result.source_envelope.source is SourceKind.TEXT_SOURCE
    assert result.source_envelope.provenance.source_sha256 == result.source_envelope.content_sha256
    assert (
        "".join(
            segment.canonical_text for segment in render_projection(result.source_envelope).segments
        )
        in result.source_envelope.content.inline_text
    )
    assert replay.replayed
    assert replay.canonical_bytes == result.canonical_bytes
    assert [item.source for item in await store.replay("s1")] == [
        SourceKind.CALL,
        SourceKind.TEXT_SOURCE,
        SourceKind.CALL,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../secret.md", "/tmp/secret.md", "source.txt"])
async def test_read_source_denies_unsafe_paths(tmp_path, path: str) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    store = EventStore(tmp_path / f"{path.replace('/', '_')}.sqlite3")
    await store.migrate()

    with pytest.raises(ToolDeniedError):
        await ToolGateway(store).read_source(
            call_id=f"call-{path}",
            idempotency_key=f"idem-{path}",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": path},
        )


@pytest.mark.asyncio
async def test_read_source_denies_symlink_stale_grant_and_cancellation(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (source_root / "escape.md").symlink_to(outside)
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    gateway = ToolGateway(store)

    with pytest.raises(ToolDeniedError):
        await gateway.read_source(
            call_id="escape",
            idempotency_key="escape",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": "escape.md"},
        )
    with pytest.raises(ToolDeniedError):
        await gateway.read_source(
            call_id="stale",
            idempotency_key="stale",
            session_id="s1",
            generation=1,
            grant=grant(source_root, generation=0),
            raw_arguments={"path": "escape.md"},
        )

    (source_root / "safe.md").write_text("safe")
    cancelled = asyncio.Event()
    cancelled.set()
    with pytest.raises(ToolCancelledError):
        await gateway.read_source(
            call_id="cancelled",
            idempotency_key="cancelled",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": "safe.md"},
            cancelled=cancelled,
        )
