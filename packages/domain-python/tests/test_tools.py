import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import radcliffe_domain.tools as tool_module
from radcliffe_domain.envelope import SourceKind
from radcliffe_domain.projection import render_projection
from radcliffe_domain.store import EventStore
from radcliffe_domain.tools import (
    CapabilityGrant,
    ToolCancelledError,
    ToolDeniedError,
    ToolGateway,
)


def grant(
    root,
    *,
    generation: int = 1,
    revoked: bool = False,
    allowed_tools: frozenset[str] = frozenset({"read_source"}),
) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant-1",
        session_id="s1",
        generation=generation,
        readable_roots=(root,),
        allowed_tools=allowed_tools,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        revoked=revoked,
    )


@pytest.mark.asyncio
async def test_read_source_is_exact_auditable_and_idempotent(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    fixture = source_root / "demo.md"
    fixture.write_bytes(b"# Canonical\n\nRead me exactly.\n")
    requested_path = "DEMO.md" if (source_root / "DEMO.md").exists() else "demo.md"
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    gateway = ToolGateway(store)

    result = await gateway.read_source(
        call_id="call-1",
        idempotency_key="idem-1",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": requested_path},
    )
    replay = await gateway.read_source(
        call_id="call-1",
        idempotency_key="idem-1",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": requested_path},
    )

    assert result.canonical_bytes == fixture.read_bytes()
    assert result.source_envelope.source is SourceKind.TEXT_SOURCE
    assert result.source_envelope.provenance.display_name == "demo.md"
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
        SourceKind.CALL,
    ]


@pytest.mark.asyncio
async def test_read_source_applies_rate_as_metadata_without_changing_source_identity(
    tmp_path,
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    fixture = source_root / "demo.md"
    fixture.write_bytes(b"First exact sentence. Second exact sentence.\n")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()

    result = await ToolGateway(store).read_source(
        call_id="call-rate",
        idempotency_key="idem-rate",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": "demo.md", "rate": 1.333},
    )
    replay = await ToolGateway(store).read_source(
        call_id="call-rate",
        idempotency_key="idem-rate",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": "demo.md", "rate": 1.333},
    )
    plan = render_projection(result.source_envelope)

    assert result.canonical_bytes == fixture.read_bytes()
    assert result.source_envelope.content.canonical_bytes() == fixture.read_bytes()
    assert result.source_envelope.content_sha256 == result.source_envelope.provenance.source_sha256
    assert [segment.delivery.multiplier for segment in plan.segments] == [1.333, 1.333]
    assert "1.333" not in result.source_envelope.content.inline_text
    assert "rate" not in result.call_envelope.content.inline_text
    assert replay.replayed
    assert render_projection(replay.source_envelope) == plan
    with pytest.raises(ToolDeniedError, match="different parameters"):
        await ToolGateway(store).read_source(
            call_id="call-rate",
            idempotency_key="idem-rate",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": "demo.md", "rate": 0.75},
        )


@pytest.mark.asyncio
async def test_read_source_rejects_unbounded_rate_before_audit_or_io(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "demo.md").write_text("Canonical.")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()

    with pytest.raises(ValueError, match="speech rate"):
        await ToolGateway(store).read_source(
            call_id="call-rate",
            idempotency_key="idem-rate",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": "demo.md", "rate": 1.51},
        )

    assert await store.replay("s1") == ()


@pytest.mark.asyncio
async def test_read_source_resolves_narrow_spoken_markdown_filename(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    fixture = source_root / "radcliffe-demo.md"
    fixture.write_text("Canonical spoken path source.\n")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()

    result = await ToolGateway(store).read_source(
        call_id="spoken-path",
        idempotency_key="spoken-path",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": "Radcliffe dash demo dot M D"},
    )

    assert result.canonical_bytes == fixture.read_bytes()
    assert result.source_envelope.provenance.display_name == "radcliffe-demo.md"
    assert result.call_envelope.content.inline_text == ('{"path":"Radcliffe dash demo dot M D"}')


@pytest.mark.asyncio
async def test_read_source_denies_missing_markdown_as_a_tool_decision(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()

    with pytest.raises(ToolDeniedError, match="readable roots"):
        await ToolGateway(store).read_source(
            call_id="missing",
            idempotency_key="missing",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": "missing.md"},
        )


@pytest.mark.asyncio
async def test_read_source_prefers_exact_name_over_case_folded_ambiguity(tmp_path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    exact = source_root / "demo.md"
    exact.write_text("Exact case wins.\n")
    differently_cased = source_root / "DEMO.md"
    try:
        differently_cased.write_text("Other case.\n")
    except OSError:
        pytest.skip("filesystem does not support case-distinct filenames")
    if exact.samefile(differently_cased):
        pytest.skip("filesystem is case-insensitive")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()

    result = await ToolGateway(store).read_source(
        call_id="exact-case",
        idempotency_key="exact-case",
        session_id="s1",
        generation=1,
        grant=grant(source_root),
        raw_arguments={"path": "demo.md"},
    )

    assert result.canonical_bytes == b"Exact case wins.\n"

    with pytest.raises(ToolDeniedError, match="readable roots"):
        await ToolGateway(store).read_source(
            call_id="ambiguous-case",
            idempotency_key="ambiguous-case",
            session_id="s1",
            generation=1,
            grant=grant(source_root),
            raw_arguments={"path": "DeMo.md"},
        )


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


@pytest.mark.asyncio
async def test_list_sources_is_bounded_private_auditable_and_idempotent(
    tmp_path, monkeypatch
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "Zeta.md").write_text("zeta")
    nested = source_root / "notes"
    nested.mkdir()
    (nested / "Alpha.MD").write_text("alpha")
    (source_root / "ignore.txt").write_text("not Markdown")
    (source_root / ".private.md").write_text("hidden")
    hidden = source_root / ".hidden"
    hidden.mkdir()
    (hidden / "secret.md").write_text("hidden")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (source_root / "escape.md").symlink_to(outside)
    too_deep = source_root / "one" / "two" / "three" / "four"
    too_deep.mkdir(parents=True)
    (too_deep / "deep.md").write_text("deep")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    gateway = ToolGateway(store)
    source_grant = grant(
        source_root,
        allowed_tools=frozenset({"list_sources", "read_source"}),
    )

    result = await gateway.list_sources(
        call_id="catalog",
        idempotency_key="catalog",
        session_id="s1",
        generation=1,
        grant=source_grant,
        raw_arguments={},
    )
    replay = await gateway.list_sources(
        call_id="catalog-retry",
        idempotency_key="catalog",
        session_id="s1",
        generation=1,
        grant=source_grant,
        raw_arguments={},
    )

    assert result.paths == ("notes/Alpha.MD", "Zeta.md")
    assert not result.truncated
    assert replay.replayed
    assert replay.paths == result.paths
    assert str(tmp_path) not in result.result_envelope.content.inline_text
    assert [item.source for item in await store.replay("s1")] == [
        SourceKind.CALL,
        SourceKind.CALL,
    ]

    with pytest.raises(ToolDeniedError, match="reused with different parameters"):
        await gateway.read_source(
            call_id="catalog",
            idempotency_key="different-operation",
            session_id="s1",
            generation=1,
            grant=source_grant,
            raw_arguments={"path": "Zeta.md"},
        )

    monkeypatch.setattr(tool_module, "MAX_SOURCE_CATALOG_ENTRIES", 1)
    limited = await gateway.list_sources(
        call_id="limited-catalog",
        idempotency_key="limited-catalog",
        session_id="s1",
        generation=1,
        grant=source_grant,
        raw_arguments={},
    )
    assert limited.paths == ("notes/Alpha.MD",)
    assert limited.truncated


@pytest.mark.asyncio
async def test_list_sources_requires_its_own_capability_and_excludes_ambiguity(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "demo.md").write_text("first")
    (second / "DEMO.md").write_text("second")
    (second / "unique.md").write_text("unique")
    store = EventStore(tmp_path / "events.sqlite3")
    await store.migrate()
    gateway = ToolGateway(store)

    with pytest.raises(ToolDeniedError, match="list_sources is not granted"):
        await gateway.list_sources(
            call_id="not-granted",
            idempotency_key="not-granted",
            session_id="s1",
            generation=1,
            grant=grant(first),
            raw_arguments={},
        )

    catalog_grant = CapabilityGrant(
        grant_id="catalog-grant",
        session_id="s1",
        generation=1,
        readable_roots=(first, second),
        allowed_tools=frozenset({"list_sources"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    result = await gateway.list_sources(
        call_id="ambiguous",
        idempotency_key="ambiguous",
        session_id="s1",
        generation=1,
        grant=catalog_grant,
        raw_arguments={},
    )

    assert result.paths == ("unique.md",)
