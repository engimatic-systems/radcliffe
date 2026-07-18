from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from radcliffe_domain.envelope import MessageEnvelope, Sensitivity, SourceKind

RENDER_PROJECTION_VERSION = "render-v2"
CONTEXT_PROJECTION_VERSION = "context-v2"
TURN_ID_METADATA = "radcliffe.turn_id"
TURN_EVENT_METADATA = "radcliffe.event"
TURN_DISPOSITION_METADATA = "radcliffe.turn_disposition"
TURN_DISPOSITION_EVENT = "turn_disposition"
CONFIGURATION_EVENT = "configuration_changed"
EXCLUDED_TURN_DISPOSITIONS = frozenset({"interrupted", "superseded", "failed"})
DELIVERY_RATE_METADATA_PREFIX = "radcliffe.delivery.rate."
DELIVERY_SUMMARY_METADATA = "radcliffe.delivery.summary"
DELIVERY_METADATA_PREFIX = "radcliffe.delivery."
MINIMUM_SPEECH_RATE = 0.75
MAXIMUM_SPEECH_RATE = 1.5
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(?:[\"')\]]*)\s+")


class SpeechRatePreset(StrEnum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"
    VERY_FAST = "very_fast"


_PRESET_MULTIPLIERS = {
    SpeechRatePreset.SLOW: 0.75,
    SpeechRatePreset.NORMAL: 1.0,
    SpeechRatePreset.FAST: 1.25,
    SpeechRatePreset.VERY_FAST: 1.5,
}


@dataclass(frozen=True, slots=True)
class SpeechDelivery:
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.multiplier)
            or self.multiplier < MINIMUM_SPEECH_RATE
            or self.multiplier > MAXIMUM_SPEECH_RATE
        ):
            raise ValueError(
                f"speech rate must be between {MINIMUM_SPEECH_RATE}x and {MAXIMUM_SPEECH_RATE}x"
            )

    @classmethod
    def parse(cls, value: object) -> Self:
        if value is None:
            return cls()
        if isinstance(value, bool):
            raise ValueError("speech rate must be a named preset or numeric multiplier")
        if isinstance(value, int | float):
            return cls(round(float(value), 3))
        if not isinstance(value, str):
            raise ValueError("speech rate must be a named preset or numeric multiplier")
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        try:
            preset = SpeechRatePreset(normalized)
        except ValueError:
            numeric = normalized.removesuffix("x")
            try:
                return cls(round(float(numeric), 3))
            except ValueError as error:
                raise ValueError("unsupported speech rate") from error
        return cls(_PRESET_MULTIPLIERS[preset])

    @property
    def is_default(self) -> bool:
        return self.multiplier == 1.0

    @property
    def metadata_value(self) -> str:
        return f"{self.multiplier:.3f}".rstrip("0").rstrip(".") + "x"

    @property
    def accessible_label(self) -> str:
        named = {
            0.75: "Slow (0.75×)",
            1.0: "Normal (1×)",
            1.25: "Fast (1.25×)",
            1.5: "Very fast (1.5×)",
        }.get(self.multiplier)
        return named or f"{self.metadata_value.removesuffix('x')}× speed"

    @property
    def local_words_per_minute(self) -> int:
        return round(180 * self.multiplier)


@dataclass(frozen=True, slots=True)
class RenderSegment:
    segment_id: str
    boundary_index: int
    canonical_text: str
    delivery: SpeechDelivery = SpeechDelivery()
    delivery_overridden: bool = False


@dataclass(frozen=True, slots=True)
class RenderPlan:
    projection_version: str
    message_id: str
    speak: bool
    provenance_label: str
    segments: tuple[RenderSegment, ...]
    delivery_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ContextPacket:
    projection_version: str
    message_id: str
    role: str
    content: str
    source: SourceKind
    annotations: tuple[tuple[str, str], ...]
    include: bool

    def to_wire(self, timestamp_ms: int) -> dict[str, object]:
        return {
            "projectionVersion": self.projection_version,
            "messageId": self.message_id,
            "role": self.role,
            "content": self.content,
            "source": self.source.value,
            "annotations": [list(item) for item in self.annotations],
            "include": self.include,
            "timestamp": timestamp_ms,
        }


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


def delivery_metadata_key(boundary_index: int) -> str:
    if boundary_index < 0:
        raise ValueError("delivery boundary cannot be negative")
    return f"{DELIVERY_RATE_METADATA_PREFIX}{boundary_index}"


def delivery_summary(metadata: Mapping[str, str]) -> str | None:
    labels: list[str] = []
    for key, value in metadata.items():
        if not key.startswith(DELIVERY_RATE_METADATA_PREFIX):
            continue
        delivery = SpeechDelivery.parse(value)
        if not delivery.is_default and delivery.accessible_label not in labels:
            labels.append(delivery.accessible_label)
    if not labels:
        return None
    return labels[0] if len(labels) == 1 else "Mixed speech rates"


def with_delivery_rate(
    envelope: MessageEnvelope,
    boundary_indices: tuple[int, ...],
    value: SpeechRatePreset | str | int | float,
) -> MessageEnvelope:
    delivery = SpeechDelivery.parse(value)
    sentence_count = len(_sentences(envelope.content.inline_text or ""))
    if any(boundary_index >= sentence_count for boundary_index in boundary_indices):
        raise ValueError("delivery boundary exceeds the render plan")
    metadata = dict(envelope.metadata)
    for boundary_index in boundary_indices:
        key = delivery_metadata_key(boundary_index)
        metadata[key] = delivery.metadata_value
    summary = delivery_summary(metadata)
    if summary is None:
        metadata.pop(DELIVERY_SUMMARY_METADATA, None)
    else:
        metadata[DELIVERY_SUMMARY_METADATA] = summary
    return MessageEnvelope.model_validate(
        {
            **envelope.model_dump(warnings=False),
            "metadata": metadata,
        }
    )


def with_delivery_rate_for_all(
    envelope: MessageEnvelope,
    value: SpeechRatePreset | str | int | float,
) -> MessageEnvelope:
    return with_delivery_rate(
        envelope,
        tuple(range(len(_sentences(envelope.content.inline_text or "")))),
        value,
    )


def render_projection(envelope: MessageEnvelope) -> RenderPlan:
    text = envelope.content.inline_text or ""
    speak = envelope.source in {SourceKind.MODEL, SourceKind.TEXT_SOURCE}
    segments: tuple[RenderSegment, ...] = ()
    if speak:
        rendered: list[RenderSegment] = []
        for index, sentence in enumerate(_sentences(text)):
            metadata_key = delivery_metadata_key(index)
            rendered.append(
                RenderSegment(
                    segment_id=f"{envelope.message_id}:{index}",
                    boundary_index=index,
                    canonical_text=sentence,
                    delivery=SpeechDelivery.parse(envelope.metadata.get(metadata_key)),
                    delivery_overridden=metadata_key in envelope.metadata,
                )
            )
        segments = tuple(rendered)
    label = envelope.provenance.display_name or envelope.provenance.locator
    return RenderPlan(
        projection_version=RENDER_PROJECTION_VERSION,
        message_id=envelope.message_id,
        speak=speak and bool(segments),
        provenance_label=label,
        segments=segments,
        delivery_summary=delivery_summary(envelope.metadata),
    )


def context_projection(envelope: MessageEnvelope) -> ContextPacket:
    role = {
        SourceKind.USER: "user",
        SourceKind.MODEL: "assistant",
        SourceKind.CALL: "tool",
        SourceKind.TEXT_SOURCE: "source",
        SourceKind.SYSTEM: "system",
    }[envelope.source]
    event = envelope.metadata.get(TURN_EVENT_METADATA)
    is_disposition = event == TURN_DISPOSITION_EVENT
    is_configuration = event == CONFIGURATION_EVENT
    include = (
        envelope.sensitivity is not Sensitivity.SECRET
        and not is_disposition
        and not is_configuration
    )
    text = envelope.content.inline_text or ""
    if envelope.sensitivity is Sensitivity.SECRET:
        text = "[redacted: secret envelope]"
    elif is_disposition:
        text = "[excluded: turn disposition audit]"
    elif is_configuration:
        text = "[excluded: configuration audit]"
    annotations = tuple(
        sorted(
            {
                "projection": CONTEXT_PROJECTION_VERSION,
                "source": envelope.source.value,
                "locator": envelope.provenance.locator,
                "permission_basis": envelope.provenance.permission_basis,
                **{
                    key: value
                    for key, value in envelope.metadata.items()
                    if not key.startswith(DELIVERY_METADATA_PREFIX)
                },
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
