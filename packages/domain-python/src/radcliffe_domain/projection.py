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
SESSION_TRANSITION_EVENT = "session_transition"
PROVIDER_BUDGET_EVENT = "provider_budget_exhausted"
TOOL_FAILURE_EVENT = "tool_failure"
BACKGROUND_JOB_REFERENCE_EVENT = "background_job_reference"
BACKGROUND_JOB_ANNOUNCEMENT_EVENT = "background_job_announcement"
BACKGROUND_JOB_OUTCOME_EVENT = "background_job_outcome"
SOURCE_PLAYBACK_EVENT = "source_playback"
SOURCE_MESSAGE_ID_METADATA = "radcliffe.source.message_id"
SOURCE_CONTENT_SHA256_METADATA = "radcliffe.source.content_sha256"
PLAYBACK_BOUNDARY_INDEX_METADATA = "radcliffe.playback.boundary_index"
PLAYBACK_COMPLETE_METADATA = "radcliffe.playback.complete"
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


_LINK_SHAPE = r"\[[^\]\n]*\]\([^)\n]*\)"
# A word run straight into a link, e.g. "affects[tons of systems](...)" — unwrapping the
# anchor without repair would say "affectstons".
_LINK_GLUED_BEFORE = re.compile(rf"(\w)(?={_LINK_SHAPE})")
_LINK_GLUED_AFTER = re.compile(rf"({_LINK_SHAPE})(\w)")
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
_INLINE_LINK = re.compile(r"(?<!!)\[([^\]\n]*)\]\([^)\n]*\)")
_REFERENCE_LINK = re.compile(r"(?<!!)\[([^\]\n]*)\]\[[^\]\n]*\]")
_REFERENCE_DEFINITION = re.compile(r"(?m)^[ \t]*\[[^\]\n]+\]:[ \t]*\S+.*$")
_AUTOLINK = re.compile(r"<(?:https?|mailto):[^>\s]*>")
_BARE_URL = re.compile(r"(?<![\w@])(?:https?://|www\.)\S+")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_STRONG = re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")
_EMPHASIS = re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)|(?<!\w)_([^_\n]+)_(?!\w)")
# Left over after balanced pairs are resolved: unclosed "**" and combined "***". A run of
# two or more asterisks is never intended prose, unlike a lone "*", which is left alone.
_ORPHAN_EMPHASIS = re.compile(r"\*{2,}")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?)\]])")
_SPACE_AFTER_OPENING = re.compile(r"([(\[])[ \t]+")
_REPEATED_SPACE = re.compile(r"[ \t]{2,}")
_EMPTY_WRAPPER = re.compile(r"\(\s*\)|\[\s*\]")


def speakable_text(text: str) -> str:
    """Render Markdown source text as the words a listener should actually hear.

    Markup is an encoding, not the author's prose: speaking a URL or a bracket aloud is
    less faithful than speaking the words around it, not more. So link *syntax* is parsed
    and resolved to its label rather than stripped character by character — a bare
    parenthetical or a bracketed aside matches no link grammar and is left untouched.

    This never changes stored content. It applies to the render plan only, so
    canonical bytes and the source hash are unaffected.
    """

    if not text:
        return text
    cleaned = _REFERENCE_DEFINITION.sub("", text)
    original = text
    cleaned = _LINK_GLUED_BEFORE.sub(r"\1 ", cleaned)
    cleaned = _LINK_GLUED_AFTER.sub(r"\1 \2", cleaned)
    cleaned = _IMAGE.sub(lambda match: match.group(1), cleaned)
    cleaned = _INLINE_LINK.sub(lambda match: match.group(1), cleaned)
    cleaned = _REFERENCE_LINK.sub(lambda match: match.group(1), cleaned)
    cleaned = _AUTOLINK.sub("", cleaned)
    cleaned = _BARE_URL.sub("", cleaned)
    cleaned = _INLINE_CODE.sub(lambda match: match.group(1), cleaned)
    cleaned = _STRONG.sub(lambda match: match.group(1) or match.group(2), cleaned)
    cleaned = _EMPHASIS.sub(lambda match: match.group(1) or match.group(2), cleaned)
    cleaned = _ORPHAN_EMPHASIS.sub("", cleaned)
    cleaned = _EMPTY_WRAPPER.sub("", cleaned)
    if cleaned == original:
        # No markup here, so there is nothing to make speakable and nothing to repair.
        # Returning the input untouched is what keeps plain prose byte-exact: its spacing
        # belongs to the source, not to us.
        return original
    cleaned = _SPACE_AFTER_OPENING.sub(r"\1", cleaned)
    cleaned = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", cleaned)
    return _REPEATED_SPACE.sub(" ", cleaned)


def render_projection(envelope: MessageEnvelope) -> RenderPlan:
    text = envelope.content.inline_text or ""
    speak = envelope.source in {SourceKind.MODEL, SourceKind.TEXT_SOURCE}
    segments: tuple[RenderSegment, ...] = ()
    if speak:
        rendered: list[RenderSegment] = []
        # Normalize per sentence, after splitting: boundary indices stay anchored to the
        # canonical document, so positions recorded before this existed remain valid.
        normalize = envelope.source is SourceKind.TEXT_SOURCE
        for index, sentence in enumerate(_sentences(text)):
            metadata_key = delivery_metadata_key(index)
            rendered.append(
                RenderSegment(
                    segment_id=f"{envelope.message_id}:{index}",
                    boundary_index=index,
                    canonical_text=speakable_text(sentence) if normalize else sentence,
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


def heard_boundary(metadata: Mapping[str, str]) -> int | None:
    """Return the first unread boundary implied by one heard-playback envelope."""

    if metadata.get(TURN_EVENT_METADATA) != SOURCE_PLAYBACK_EVENT:
        return None
    raw_index = metadata.get(PLAYBACK_BOUNDARY_INDEX_METADATA)
    if raw_index is None:
        return None
    try:
        boundary_index = int(raw_index)
    except ValueError:
        return None
    if boundary_index < 0:
        return None
    complete = metadata.get(PLAYBACK_COMPLETE_METADATA) == "true"
    # A partially heard sentence is replayed from its own start.
    return boundary_index + 1 if complete else boundary_index


def resume_boundary(
    envelopes: tuple[MessageEnvelope, ...],
    content_sha256: bytes,
) -> int:
    """Return the first unheard boundary for a document, keyed by content rather than call.

    Identical bytes always produce an identical sentence split, so boundaries recorded
    under one read_source call remain valid for any later call reading the same source.
    """

    if not content_sha256:
        return 0
    digest = content_sha256.hex()
    boundaries = [
        boundary
        for envelope in envelopes
        if envelope.metadata.get(SOURCE_CONTENT_SHA256_METADATA) == digest
        and (boundary := heard_boundary(envelope.metadata)) is not None
    ]
    return max(boundaries, default=0)


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
    is_session_transition = event == SESSION_TRANSITION_EVENT
    is_provider_budget = event == PROVIDER_BUDGET_EVENT
    include = (
        envelope.sensitivity is not Sensitivity.SECRET
        and not is_disposition
        and not is_configuration
        and not is_session_transition
        and not is_provider_budget
    )
    text = envelope.content.inline_text or ""
    if envelope.sensitivity is Sensitivity.SECRET:
        text = "[redacted: secret envelope]"
    elif is_disposition:
        text = "[excluded: turn disposition audit]"
    elif is_configuration:
        text = "[excluded: configuration audit]"
    elif is_session_transition:
        text = "[excluded: session transition audit]"
    elif is_provider_budget:
        text = "[excluded: provider budget audit]"
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
            # A response disposition can suppress abandoned assistant/tool output, but accepted
            # user speech remains part of the chronological conversation.
            if envelope.source is SourceKind.USER:
                packet = replace(
                    packet,
                    annotations=tuple(sorted(annotations.items())),
                )
            else:
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
