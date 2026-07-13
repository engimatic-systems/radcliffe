from datetime import UTC, datetime, timedelta

import pytest
from radcliffe_domain.projection import render_projection
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import CapabilityGrant, ToolGateway


@pytest.mark.asyncio
async def test_external_source_contract_is_byte_exact_and_replayable(tmp_path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    canonical = b"First exact sentence.  Second exact sentence.\n"
    (root / "demo.md").write_bytes(canonical)
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    gateway = ToolGateway(store)
    grant = CapabilityGrant(
        grant_id="conformance-grant",
        session_id="conformance-session",
        generation=1,
        readable_roots=(root,),
        allowed_tools=frozenset({"read_source"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = await gateway.read_source(
        call_id="conformance-call",
        idempotency_key="conformance-call",
        session_id="conformance-session",
        generation=1,
        grant=grant,
        raw_arguments={"path": "demo.md"},
    )
    rendered = b"".join(
        segment.canonical_text.encode()
        for segment in render_projection(result.source_envelope).segments
    )

    assert rendered == canonical
    assert (
        await gateway.read_source(
            call_id="conformance-call",
            idempotency_key="conformance-call",
            session_id="conformance-session",
            generation=1,
            grant=grant,
            raw_arguments={"path": "demo.md"},
        )
    ).replayed
