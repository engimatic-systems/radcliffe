from __future__ import annotations

import json
import os
import stat
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


class PromotedJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    job_id: str = Field(alias="jobId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    summary: str = Field(min_length=1, max_length=4_000)
    artifacts: tuple[PromotedArtifact, ...] = Field(default=(), max_length=20)


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
