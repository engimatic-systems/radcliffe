from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self
from uuid import uuid4

from google.protobuf.timestamp_pb2 import Timestamp
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from radcliffe_protocol.radcliffe.v2 import protocol_pb2


class SourceKind(StrEnum):
    USER = "user"
    MODEL = "model"
    CALL = "call"
    TEXT_SOURCE = "text_source"
    SYSTEM = "system"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SECRET = "secret"


class Content(BaseModel):
    model_config = ConfigDict(frozen=True)

    mime_type: str = "text/plain"
    inline_text: str | None = None
    blob_uri: str | None = None
    byte_length: int

    @model_validator(mode="after")
    def exactly_one_value(self) -> Self:
        if (self.inline_text is None) == (self.blob_uri is None):
            raise ValueError("content requires exactly one of inline_text or blob_uri")
        if self.inline_text is not None and len(self.inline_text.encode()) != self.byte_length:
            raise ValueError("byte_length does not match UTF-8 content")
        return self

    def canonical_bytes(self) -> bytes:
        if self.inline_text is None:
            raise ValueError("blob content must be resolved before hashing")
        return self.inline_text.encode()


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    locator: str = ""
    display_name: str = ""
    extractor: str = ""
    extractor_version: str = ""
    source_sha256: bytes = b""
    permission_basis: str = ""


_SOURCE_TO_PROTO = {
    SourceKind.USER: protocol_pb2.SOURCE_KIND_USER,
    SourceKind.MODEL: protocol_pb2.SOURCE_KIND_MODEL,
    SourceKind.CALL: protocol_pb2.SOURCE_KIND_CALL,
    SourceKind.TEXT_SOURCE: protocol_pb2.SOURCE_KIND_TEXT_SOURCE,
    SourceKind.SYSTEM: protocol_pb2.SOURCE_KIND_SYSTEM,
}
_SOURCE_FROM_PROTO = {value: key for key, value in _SOURCE_TO_PROTO.items()}
_SENSITIVITY_TO_PROTO = {
    Sensitivity.PUBLIC: protocol_pb2.SENSITIVITY_PUBLIC,
    Sensitivity.PRIVATE: protocol_pb2.SENSITIVITY_PRIVATE,
    Sensitivity.SECRET: protocol_pb2.SENSITIVITY_SECRET,
}
_SENSITIVITY_FROM_PROTO = {value: key for key, value in _SENSITIVITY_TO_PROTO.items()}


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    sequence: int = Field(ge=1)
    source: SourceKind
    content: Content
    content_sha256: bytes
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, str] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    sensitivity: Sensitivity = Sensitivity.PRIVATE

    @field_validator("created_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(sorted(value.items())))

    @model_validator(mode="after")
    def hash_matches_content(self) -> Self:
        if self.content.inline_text is not None:
            actual = hashlib.sha256(self.content.canonical_bytes()).digest()
            if actual != self.content_sha256:
                raise ValueError("content_sha256 does not match canonical content")
        return self

    @classmethod
    def text(
        cls,
        *,
        session_id: str,
        sequence: int,
        source: SourceKind,
        text: str,
        metadata: Mapping[str, str] | None = None,
        provenance: Provenance | None = None,
        sensitivity: Sensitivity = Sensitivity.PRIVATE,
        message_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Self:
        raw = text.encode()
        values: dict[str, Any] = {
            "session_id": session_id,
            "sequence": sequence,
            "source": source,
            "content": Content(inline_text=text, byte_length=len(raw)),
            "content_sha256": hashlib.sha256(raw).digest(),
            "metadata": metadata or {},
            "provenance": provenance or Provenance(),
            "sensitivity": sensitivity,
        }
        if message_id is not None:
            values["message_id"] = message_id
        if created_at is not None:
            values["created_at"] = created_at
        return cls.model_validate(values)

    def to_proto(self) -> protocol_pb2.MessageEnvelope:
        timestamp = Timestamp()
        timestamp.FromDatetime(self.created_at)
        content = protocol_pb2.ContentRef(
            mime_type=self.content.mime_type,
            byte_length=self.content.byte_length,
        )
        if self.content.inline_text is not None:
            content.inline_text = self.content.inline_text
        else:
            assert self.content.blob_uri is not None
            content.blob_uri = self.content.blob_uri
        return protocol_pb2.MessageEnvelope(
            schema_version=self.schema_version,
            message_id=self.message_id,
            session_id=self.session_id,
            sequence=self.sequence,
            source=_SOURCE_TO_PROTO[self.source],
            content=content,
            content_sha256=self.content_sha256,
            created_at=timestamp,
            metadata=dict(self.metadata),
            provenance=protocol_pb2.Provenance(
                locator=self.provenance.locator,
                display_name=self.provenance.display_name,
                extractor=self.provenance.extractor,
                extractor_version=self.provenance.extractor_version,
                source_sha256=self.provenance.source_sha256,
                permission_basis=self.provenance.permission_basis,
            ),
            sensitivity=_SENSITIVITY_TO_PROTO[self.sensitivity],
        )

    @classmethod
    def from_proto(cls, value: protocol_pb2.MessageEnvelope) -> Self:
        which = value.content.WhichOneof("value")
        return cls(
            schema_version=value.schema_version,
            message_id=value.message_id,
            session_id=value.session_id,
            sequence=value.sequence,
            source=_SOURCE_FROM_PROTO[value.source],
            content=Content(
                mime_type=value.content.mime_type,
                byte_length=value.content.byte_length,
                inline_text=value.content.inline_text if which == "inline_text" else None,
                blob_uri=value.content.blob_uri if which == "blob_uri" else None,
            ),
            content_sha256=value.content_sha256,
            created_at=value.created_at.ToDatetime(tzinfo=UTC),
            metadata=dict(value.metadata),
            provenance=Provenance(
                locator=value.provenance.locator,
                display_name=value.provenance.display_name,
                extractor=value.provenance.extractor,
                extractor_version=value.provenance.extractor_version,
                source_sha256=value.provenance.source_sha256,
                permission_basis=value.provenance.permission_basis,
            ),
            sensitivity=_SENSITIVITY_FROM_PROTO[value.sensitivity],
        )
