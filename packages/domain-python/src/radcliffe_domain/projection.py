from __future__ import annotations

import re
from dataclasses import dataclass, replace

from radcliffe_domain.envelope import MessageEnvelope, Sensitivity, SourceKind

RENDER_PROJECTION_VERSION = "render-v1"
CONTEXT_PROJECTION_VERSION = "context-v2"
TURN_ID_METADATA = "radcliffe.turn_id"
TURN_EVENT_METADATA = "radcliffe.event"
TURN_DISPOSITION_METADATA = "radcliffe.turn_disposition"
TURN_DISPOSITION_EVENT = "turn_disposition"
EXCLUDED_TURN_DISPOSITIONS = frozenset({"interrupted", "superseded", "failed"})
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
    if not text:
        return ()
    boundaries: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        boundaries.append(text[start : match.end()])
        start = match.end()
    if start < len(text):
        boundaries.append(text[start:])
    return tuple(part for part in boundaries if part)


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
    is_disposition = envelope.metadata.get(TURN_EVENT_METADATA) == TURN_DISPOSITION_EVENT
    include = envelope.sensitivity is not Sensitivity.SECRET and not is_disposition
    text = envelope.content.inline_text or ""
    if envelope.sensitivity is Sensitivity.SECRET:
        text = "[redacted: secret envelope]"
    elif is_disposition:
        text = "[excluded: turn disposition audit]"
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


def context_replay(envelopes: tuple[MessageEnvelope, ...]) -> tuple[ContextPacket, ...]:
    dispositions: dict[str, str] = {}
    for envelope in envelopes:
        if envelope.metadata.get(TURN_EVENT_METADATA) != TURN_DISPOSITION_EVENT:
            continue
        turn_id = envelope.metadata.get(TURN_ID_METADATA)
        disposition = envelope.metadata.get(TURN_DISPOSITION_METADATA)
        if turn_id and disposition:
            dispositions[turn_id] = disposition

    packets: list[ContextPacket] = []
    for envelope in envelopes:
        packet = context_projection(envelope)
        turn_id = envelope.metadata.get(TURN_ID_METADATA)
        disposition = dispositions.get(turn_id, "") if turn_id else ""
        if disposition in EXCLUDED_TURN_DISPOSITIONS:
            annotations = dict(packet.annotations)
            annotations[TURN_DISPOSITION_METADATA] = disposition
            packet = replace(
                packet,
                content=f"[excluded: {disposition} turn]",
                annotations=tuple(sorted(annotations.items())),
                include=False,
            )
        packets.append(packet)
    return tuple(packets)


def project_replay(
    envelopes: tuple[MessageEnvelope, ...],
) -> tuple[tuple[RenderPlan, ...], tuple[ContextPacket, ...]]:
    return (
        tuple(render_projection(envelope) for envelope in envelopes),
        context_replay(envelopes),
    )
