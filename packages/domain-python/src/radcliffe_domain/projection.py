from __future__ import annotations

import re
from dataclasses import dataclass

from radcliffe_domain.envelope import MessageEnvelope, Sensitivity, SourceKind

RENDER_PROJECTION_VERSION = "render-v1"
CONTEXT_PROJECTION_VERSION = "context-v1"
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(?:[\"')\]]*)\s+")


@dataclass(frozen=True, slots=True)
class RenderSegment:
    segment_id: str
    boundary_index: int
    canonical_text: str


@dataclass(frozen=True, slots=True)
class RenderPlan:
    projection_version: str
    message_id: str
    speak: bool
    provenance_label: str
    segments: tuple[RenderSegment, ...]


@dataclass(frozen=True, slots=True)
class ContextPacket:
    projection_version: str
    message_id: str
    role: str
    content: str
    source: SourceKind
    annotations: tuple[tuple[str, str], ...]
    include: bool


def _sentences(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    if not stripped:
        return ()
    return tuple(part for part in _SENTENCE_BOUNDARY.split(stripped) if part)


def render_projection(envelope: MessageEnvelope) -> RenderPlan:
    text = envelope.content.inline_text or ""
    speak = envelope.source in {SourceKind.MODEL, SourceKind.TEXT_SOURCE}
    segments = (
        tuple(
            RenderSegment(
                segment_id=f"{envelope.message_id}:{index}",
                boundary_index=index,
                canonical_text=sentence,
            )
            for index, sentence in enumerate(_sentences(text))
        )
        if speak
        else ()
    )
    label = envelope.provenance.display_name or envelope.provenance.locator
    return RenderPlan(
        projection_version=RENDER_PROJECTION_VERSION,
        message_id=envelope.message_id,
        speak=speak and bool(segments),
        provenance_label=label,
        segments=segments,
    )


def context_projection(envelope: MessageEnvelope) -> ContextPacket:
    role = {
        SourceKind.USER: "user",
        SourceKind.MODEL: "assistant",
        SourceKind.CALL: "tool",
        SourceKind.TEXT_SOURCE: "source",
        SourceKind.SYSTEM: "system",
    }[envelope.source]
    include = envelope.sensitivity is not Sensitivity.SECRET
    text = envelope.content.inline_text or ""
    if not include:
        text = "[redacted: secret envelope]"
    annotations = tuple(
        sorted(
            {
                "projection": CONTEXT_PROJECTION_VERSION,
                "source": envelope.source.value,
                "locator": envelope.provenance.locator,
                "permission_basis": envelope.provenance.permission_basis,
                **dict(envelope.metadata),
            }.items()
        )
    )
    return ContextPacket(
        projection_version=CONTEXT_PROJECTION_VERSION,
        message_id=envelope.message_id,
        role=role,
        content=text,
        source=envelope.source,
        annotations=annotations,
        include=include,
    )


def project_replay(
    envelopes: tuple[MessageEnvelope, ...],
) -> tuple[tuple[RenderPlan, ...], tuple[ContextPacket, ...]]:
    return (
        tuple(render_projection(envelope) for envelope in envelopes),
        tuple(context_projection(envelope) for envelope in envelopes),
    )
