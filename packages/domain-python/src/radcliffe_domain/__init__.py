"""Transport-independent conversation, projection, and tool domain."""

from radcliffe_domain.envelope import (
    Content,
    MessageEnvelope,
    Provenance,
    Sensitivity,
    SourceKind,
)

VERSION = "0.1.0"

__all__ = [
    "VERSION",
    "Content",
    "MessageEnvelope",
    "Provenance",
    "Sensitivity",
    "SourceKind",
]
