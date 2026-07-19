import os

from radcliffe_control.job_results import (
    PromotedArtifact,
    PromotedJobResult,
    read_promoted_result,
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
