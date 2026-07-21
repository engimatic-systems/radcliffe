from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

RESULT_FILENAME = "promoted-result.json"
RESULT_LIMIT_BYTES = 64 * 1024


class PromotedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    artifact_id: str = Field(alias="artifactId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    path: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_length: int = Field(alias="byteLength", ge=0, le=64 * 1024 * 1024)


class PromotedSource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    entity_id: str = Field(alias="entityId", pattern=r"^source:[a-f0-9]{16}$")
    name: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=2_048)
    artifact_id: str = Field(alias="artifactId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    path: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\.md$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_length: int = Field(alias="byteLength", ge=1, le=1024 * 1024)


class PromotedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    entity_id: str = Field(alias="entityId", pattern=r"^finding:[a-f0-9]{16}$")
    text: str = Field(min_length=1, max_length=1_000)
    source_entity_ids: tuple[str, ...] = Field(
        alias="sourceEntityIds",
        min_length=1,
        max_length=10,
    )
    inference: bool = False

    @field_validator("source_entity_ids")
    @classmethod
    def valid_source_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not all(re.fullmatch(r"source:[a-f0-9]{16}", value) for value in values):
            raise ValueError("finding source entity IDs are invalid")
        if len(set(values)) != len(values):
            raise ValueError("finding source entity IDs must be unique")
        return values


class PromotedJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    job_id: str = Field(alias="jobId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    summary: str = Field(min_length=1, max_length=4_000)
    artifacts: tuple[PromotedArtifact, ...] = Field(default=(), max_length=20)
    sources: tuple[PromotedSource, ...] = Field(default=(), max_length=20)
    findings: tuple[PromotedFinding, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def valid_entity_graph(self) -> PromotedJobResult:
        source_ids = {source.entity_id for source in self.sources}
        finding_ids = {finding.entity_id for finding in self.findings}
        if len(source_ids) != len(self.sources) or len(finding_ids) != len(self.findings):
            raise ValueError("promoted entity IDs must be unique")
        if any(
            not set(finding.source_entity_ids).issubset(source_ids) for finding in self.findings
        ):
            raise ValueError("promoted finding cites an unavailable source")
        return self


@dataclass(frozen=True, slots=True)
class PromotedSourceFile:
    source: PromotedSource
    root: Path
    path: Path
    canonical_bytes: bytes


def generated_artifact_source(
    job_id: str,
    artifact: PromotedArtifact,
) -> PromotedSource | None:
    if (
        not artifact.path.casefold().endswith(".md")
        or artifact.byte_length < 1
        or artifact.byte_length > 1024 * 1024
    ):
        return None
    name = artifact.path
    locator = f"radcliffe-job-output:{job_id}:{artifact.path}"
    digest = hashlib.sha256(
        (f"{job_id}\0{name}\0{locator}\0{artifact.sha256}").encode()
    ).hexdigest()[:16]
    return PromotedSource(
        entityId=f"source:{digest}",
        name=name,
        locator=locator,
        artifactId=artifact.artifact_id,
        path=artifact.path,
        sha256=artifact.sha256,
        byteLength=artifact.byte_length,
    )


def write_promoted_result(workspace: Path, result: PromotedJobResult) -> Path:
    workspace = workspace.resolve()
    target = workspace / RESULT_FILENAME
    encoded = (
        json.dumps(
            result.model_dump(by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    if len(encoded) > RESULT_LIMIT_BYTES:
        raise ValueError("promoted job result is too large")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def read_promoted_result(
    workspace_root: Path,
    *,
    job_id: str,
    attempt: int,
) -> PromotedJobResult | None:
    if attempt < 1 or not job_id or "/" in job_id or "\\" in job_id:
        return None
    root = workspace_root.resolve()
    job_root = root / job_id
    raw_workspace = job_root / f"attempt-{attempt}"
    if job_root.is_symlink() or raw_workspace.is_symlink():
        return None
    workspace = raw_workspace.resolve()
    if not workspace.is_relative_to(root):
        return None
    target = workspace / RESULT_FILENAME
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > RESULT_LIMIT_BYTES:
            os.close(descriptor)
            return None
        with os.fdopen(descriptor, "rb") as source:
            payload = source.read(RESULT_LIMIT_BYTES + 1)
    except OSError:
        return None
    try:
        result = PromotedJobResult.model_validate_json(payload)
    except ValidationError:
        return None
    return result if result.job_id == job_id else None


def read_promoted_source(
    workspace_root: Path,
    *,
    job_id: str,
    attempt: int,
    entity_id: str,
) -> PromotedSourceFile | None:
    matches = tuple(
        source
        for source in read_promoted_sources(
            workspace_root,
            job_id=job_id,
            attempt=attempt,
        )
        if source.source.entity_id == entity_id
    )
    return matches[0] if len(matches) == 1 else None


def read_promoted_sources(
    workspace_root: Path,
    *,
    job_id: str,
    attempt: int,
) -> tuple[PromotedSourceFile, ...]:
    result = read_promoted_result(workspace_root, job_id=job_id, attempt=attempt)
    if result is None:
        return ()
    artifacts_by_identity = {
        (artifact.artifact_id, artifact.path): artifact for artifact in result.artifacts
    }
    sources = list(result.sources)
    declared_paths = {source.path for source in sources}
    for artifact in result.artifacts:
        if artifact.path in declared_paths:
            continue
        derived = generated_artifact_source(job_id, artifact)
        if derived is not None:
            sources.append(derived)
    promoted: list[PromotedSourceFile] = []
    for source in sources:
        selected_artifact = artifacts_by_identity.get((source.artifact_id, source.path))
        if selected_artifact is None:
            continue
        resolved = _read_promoted_source_file(
            workspace_root,
            job_id=job_id,
            attempt=attempt,
            source=source,
            artifact=selected_artifact,
        )
        if resolved is not None:
            promoted.append(resolved)
    return tuple(promoted)


def _read_promoted_source_file(
    workspace_root: Path,
    *,
    job_id: str,
    attempt: int,
    source: PromotedSource,
    artifact: PromotedArtifact,
) -> PromotedSourceFile | None:
    if (
        artifact.sha256 != source.sha256
        or artifact.byte_length != source.byte_length
        or artifact.byte_length > 1024 * 1024
    ):
        return None

    root = workspace_root.resolve()
    job_root = root / job_id
    attempt_root = job_root / f"attempt-{attempt}"
    files_root = attempt_root / "files"
    if any(path.is_symlink() for path in (job_root, attempt_root, files_root)):
        return None
    try:
        resolved_files = files_root.resolve(strict=True)
    except OSError:
        return None
    relative = Path(source.path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = resolved_files / relative
    for index in range(1, len(relative.parts) + 1):
        if (resolved_files / Path(*relative.parts[:index])).is_symlink():
            return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_relative_to(resolved_files):
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != source.byte_length:
            os.close(descriptor)
            return None
        with os.fdopen(descriptor, "rb") as content:
            canonical = content.read(1024 * 1024 + 1)
    except OSError:
        return None
    try:
        canonical.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(canonical) != source.byte_length:
        return None
    if hashlib.sha256(canonical).hexdigest() != source.sha256:
        return None
    return PromotedSourceFile(source, resolved_files, resolved, canonical)
