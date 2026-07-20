from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class PromotedJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    job_id: str = Field(alias="jobId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    summary: str = Field(min_length=1, max_length=4_000)
    artifacts: tuple[PromotedArtifact, ...] = Field(default=(), max_length=20)
    sources: tuple[PromotedSource, ...] = Field(default=(), max_length=20)


@dataclass(frozen=True, slots=True)
class PromotedSourceFile:
    source: PromotedSource
    root: Path
    path: Path
    canonical_bytes: bytes


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
    result = read_promoted_result(workspace_root, job_id=job_id, attempt=attempt)
    if result is None:
        return None
    matches = tuple(source for source in result.sources if source.entity_id == entity_id)
    if len(matches) != 1:
        return None
    source = matches[0]
    artifacts = tuple(
        artifact
        for artifact in result.artifacts
        if artifact.artifact_id == source.artifact_id and artifact.path == source.path
    )
    if len(artifacts) != 1:
        return None
    artifact = artifacts[0]
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
