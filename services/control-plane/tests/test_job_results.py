import hashlib
import os

from radcliffe_control.job_results import (
    PromotedArtifact,
    PromotedFinding,
    PromotedJobResult,
    PromotedRelatedLink,
    PromotedSource,
    read_promoted_result,
    read_promoted_source,
    read_promoted_sources,
    read_workspace_related_links,
    write_promoted_result,
)


def result(job_id: str = "job-1") -> PromotedJobResult:
    return PromotedJobResult(
        schemaVersion=1,
        jobId=job_id,
        summary="The bounded job result.",
        artifacts=(
            PromotedArtifact(
                artifactId="artifact:file:abc",
                path="report.md",
                sha256="a" * 64,
                byteLength=12,
            ),
        ),
    )


def test_promoted_result_round_trips_as_owner_only_file(tmp_path) -> None:
    root = tmp_path / "jobs"
    workspace = root / "job-1" / "attempt-1"
    workspace.mkdir(parents=True)

    target = write_promoted_result(workspace, result())

    assert target.stat().st_mode & 0o777 == 0o600
    assert read_promoted_result(root, job_id="job-1", attempt=1) == result()


def test_private_page_link_manifests_promote_bounded_related_links(tmp_path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    pages.joinpath("page-one.links.json").write_text(
        '{"schemaVersion":1,"links":['
        '{"label":"Second story","locator":"https://example.test/second"},'
        '{"label":"Duplicate","locator":"https://example.test/second"},'
        '{"label":"Unsafe","locator":"file:///etc/passwd"}]}'
    )

    assert read_workspace_related_links(tmp_path) == (
        PromotedRelatedLink(label="Second story", locator="https://example.test/second"),
    )


def test_promoted_result_reader_fails_closed_on_links_mismatch_and_oversize(tmp_path) -> None:
    root = tmp_path / "jobs"
    outside = tmp_path / "outside"
    outside.mkdir()
    write_promoted_result(outside, result())
    (root / "job-1").mkdir(parents=True)
    os.symlink(outside, root / "job-1" / "attempt-1")

    assert read_promoted_result(root, job_id="job-1", attempt=1) is None

    workspace = root / "job-2" / "attempt-1"
    workspace.mkdir(parents=True)
    write_promoted_result(workspace, result("another-job"))
    assert read_promoted_result(root, job_id="job-2", attempt=1) is None

    oversized = root / "job-3" / "attempt-1"
    oversized.mkdir(parents=True)
    oversized.joinpath("promoted-result.json").write_bytes(b"x" * (64 * 1024 + 1))
    assert read_promoted_result(root, job_id="job-3", attempt=1) is None


def test_promoted_source_is_hash_verified_beneath_the_private_attempt(tmp_path) -> None:
    root = tmp_path / "jobs"
    workspace = root / "job-source" / "attempt-1"
    files = workspace / "files"
    files.mkdir(parents=True)
    canonical = b"# Exact abstract\n\nThe retained canonical text.\n"
    path = files / "paper.md"
    path.write_bytes(canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    artifact = PromotedArtifact(
        artifactId=f"artifact:file:{digest[:16]}",
        path="paper.md",
        sha256=digest,
        byteLength=len(canonical),
    )
    source = PromotedSource(
        entityId=f"source:{digest[:16]}",
        name="Exact Abstract",
        locator="https://example.test/paper",
        artifactId=artifact.artifact_id,
        path=artifact.path,
        sha256=digest,
        byteLength=len(canonical),
    )
    finding = PromotedFinding(
        entityId=f"finding:{digest[:16]}",
        text="The retained text is the exact abstract.",
        sourceEntityIds=(source.entity_id,),
        inference=False,
    )
    write_promoted_result(
        workspace,
        PromotedJobResult(
            schemaVersion=1,
            jobId="job-source",
            summary="Promoted one exact source.",
            artifacts=(artifact,),
            sources=(source,),
            findings=(finding,),
        ),
    )

    resolved = read_promoted_source(
        root,
        job_id="job-source",
        attempt=1,
        entity_id=source.entity_id,
    )

    assert resolved is not None
    assert resolved.canonical_bytes == canonical
    assert resolved.path == path
    promoted = read_promoted_result(root, job_id="job-source", attempt=1)
    assert promoted is not None
    assert promoted.findings == (finding,)
    path.write_text("tampered")
    assert (
        read_promoted_source(
            root,
            job_id="job-source",
            attempt=1,
            entity_id=source.entity_id,
        )
        is None
    )


def test_legacy_markdown_artifact_is_a_hash_verified_readable_source(tmp_path) -> None:
    root = tmp_path / "jobs"
    workspace = root / "job-generated" / "attempt-1"
    files = workspace / "files"
    files.mkdir(parents=True)
    canonical = b"# Generated headlines\n\n1. First\n2. Second\n"
    digest = hashlib.sha256(canonical).hexdigest()
    artifact = PromotedArtifact(
        artifactId=f"artifact:file:{digest[:16]}",
        path="hacker_news_headlines.md",
        sha256=digest,
        byteLength=len(canonical),
    )
    files.joinpath(artifact.path).write_bytes(canonical)
    write_promoted_result(
        workspace,
        PromotedJobResult(
            schemaVersion=1,
            jobId="job-generated",
            summary="Generated a readable Markdown artifact.",
            artifacts=(artifact,),
        ),
    )

    sources = read_promoted_sources(root, job_id="job-generated", attempt=1)

    assert len(sources) == 1
    assert sources[0].source.name == artifact.path
    assert sources[0].source.locator.startswith("radcliffe-job-output:")
    assert sources[0].canonical_bytes == canonical
    assert (
        read_promoted_source(
            root,
            job_id="job-generated",
            attempt=1,
            entity_id=sources[0].source.entity_id,
        )
        == sources[0]
    )
