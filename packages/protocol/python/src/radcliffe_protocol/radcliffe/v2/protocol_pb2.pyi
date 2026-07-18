import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SourceKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SOURCE_KIND_UNSPECIFIED: _ClassVar[SourceKind]
    SOURCE_KIND_USER: _ClassVar[SourceKind]
    SOURCE_KIND_MODEL: _ClassVar[SourceKind]
    SOURCE_KIND_CALL: _ClassVar[SourceKind]
    SOURCE_KIND_TEXT_SOURCE: _ClassVar[SourceKind]
    SOURCE_KIND_SYSTEM: _ClassVar[SourceKind]

class Sensitivity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SENSITIVITY_UNSPECIFIED: _ClassVar[Sensitivity]
    SENSITIVITY_PUBLIC: _ClassVar[Sensitivity]
    SENSITIVITY_PRIVATE: _ClassVar[Sensitivity]
    SENSITIVITY_SECRET: _ClassVar[Sensitivity]

class ConversationState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONVERSATION_STATE_UNSPECIFIED: _ClassVar[ConversationState]
    CONVERSATION_STATE_IDLE: _ClassVar[ConversationState]
    CONVERSATION_STATE_CONNECTING: _ClassVar[ConversationState]
    CONVERSATION_STATE_LISTENING: _ClassVar[ConversationState]
    CONVERSATION_STATE_THINKING: _ClassVar[ConversationState]
    CONVERSATION_STATE_SPEAKING: _ClassVar[ConversationState]
    CONVERSATION_STATE_READING: _ClassVar[ConversationState]
    CONVERSATION_STATE_PAUSED: _ClassVar[ConversationState]
    CONVERSATION_STATE_RECONNECTING: _ClassVar[ConversationState]
    CONVERSATION_STATE_FAILED: _ClassVar[ConversationState]

class PlaybackStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PLAYBACK_STATUS_UNSPECIFIED: _ClassVar[PlaybackStatus]
    PLAYBACK_STATUS_STARTED: _ClassVar[PlaybackStatus]
    PLAYBACK_STATUS_POSITION: _ClassVar[PlaybackStatus]
    PLAYBACK_STATUS_COMPLETED: _ClassVar[PlaybackStatus]
    PLAYBACK_STATUS_FLUSHED: _ClassVar[PlaybackStatus]
    PLAYBACK_STATUS_FAILED: _ClassVar[PlaybackStatus]

class ToolOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TOOL_OUTCOME_UNSPECIFIED: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_REQUESTED: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_AUTHORIZED: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_DENIED: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_RUNNING: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_SUCCEEDED: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_CANCELLED: _ClassVar[ToolOutcome]
    TOOL_OUTCOME_FAILED: _ClassVar[ToolOutcome]

class AgentControlScope(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_CONTROL_SCOPE_UNSPECIFIED: _ClassVar[AgentControlScope]
    AGENT_CONTROL_SCOPE_SNIPPET: _ClassVar[AgentControlScope]
    AGENT_CONTROL_SCOPE_TURN: _ClassVar[AgentControlScope]
    AGENT_CONTROL_SCOPE_SESSION: _ClassVar[AgentControlScope]
    AGENT_CONTROL_SCOPE_DEVICE: _ClassVar[AgentControlScope]
    AGENT_CONTROL_SCOPE_OPERATOR_DEFAULT: _ClassVar[AgentControlScope]

class AgentControlActionId(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_CONTROL_ACTION_ID_UNSPECIFIED: _ClassVar[AgentControlActionId]
    AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE: _ClassVar[AgentControlActionId]
    AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET: _ClassVar[AgentControlActionId]
    AGENT_CONTROL_ACTION_ID_SESSION_CREATE: _ClassVar[AgentControlActionId]
    AGENT_CONTROL_ACTION_ID_SESSION_SWITCH: _ClassVar[AgentControlActionId]
    AGENT_CONTROL_ACTION_ID_SESSION_RESTART: _ClassVar[AgentControlActionId]

class AgentControlConfirmationRequirement(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_CONTROL_CONFIRMATION_REQUIREMENT_UNSPECIFIED: _ClassVar[AgentControlConfirmationRequirement]
    AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE: _ClassVar[AgentControlConfirmationRequirement]
    AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER: _ClassVar[AgentControlConfirmationRequirement]
    AGENT_CONTROL_CONFIRMATION_REQUIREMENT_OPERATOR: _ClassVar[AgentControlConfirmationRequirement]

class AgentControlDisposition(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_CONTROL_DISPOSITION_UNSPECIFIED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_REQUESTED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_AWAITING_CONFIRMATION: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_AUTHORIZED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_APPLIED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_REJECTED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_DUPLICATE: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_CANCELLED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_FAILED: _ClassVar[AgentControlDisposition]
    AGENT_CONTROL_DISPOSITION_SUPERSEDED: _ClassVar[AgentControlDisposition]

class AgentControlRejectionCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_CONTROL_REJECTION_CODE_UNSPECIFIED: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_UNSUPPORTED_SCHEMA_VERSION: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_MALFORMED: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_UNREGISTERED_ACTION: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_UNAUTHORIZED: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_INVALID_SCOPE: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_INVALID_TARGET: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_STALE_GENERATION: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_IDEMPOTENCY_CONFLICT: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_CONFLICT: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_CONFIRMATION_POLICY_MISMATCH: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_CANCELLED: _ClassVar[AgentControlRejectionCode]
    AGENT_CONTROL_REJECTION_CODE_ALREADY_APPLIED: _ClassVar[AgentControlRejectionCode]

class SpeechRate(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SPEECH_RATE_UNSPECIFIED: _ClassVar[SpeechRate]
    SPEECH_RATE_SLOW_0_75X: _ClassVar[SpeechRate]
    SPEECH_RATE_NORMAL_1_0X: _ClassVar[SpeechRate]
    SPEECH_RATE_FAST_1_25X: _ClassVar[SpeechRate]
    SPEECH_RATE_VERY_FAST_1_5X: _ClassVar[SpeechRate]

class AgentConfigurationKey(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_CONFIGURATION_KEY_UNSPECIFIED: _ClassVar[AgentConfigurationKey]
    AGENT_CONFIGURATION_KEY_RESPONSE_DETAIL: _ClassVar[AgentConfigurationKey]
    AGENT_CONFIGURATION_KEY_DEFAULT_SPEECH_RATE: _ClassVar[AgentConfigurationKey]

class ResponseDetail(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RESPONSE_DETAIL_UNSPECIFIED: _ClassVar[ResponseDetail]
    RESPONSE_DETAIL_BRIEF: _ClassVar[ResponseDetail]
    RESPONSE_DETAIL_STANDARD: _ClassVar[ResponseDetail]
    RESPONSE_DETAIL_DETAILED: _ClassVar[ResponseDetail]
SOURCE_KIND_UNSPECIFIED: SourceKind
SOURCE_KIND_USER: SourceKind
SOURCE_KIND_MODEL: SourceKind
SOURCE_KIND_CALL: SourceKind
SOURCE_KIND_TEXT_SOURCE: SourceKind
SOURCE_KIND_SYSTEM: SourceKind
SENSITIVITY_UNSPECIFIED: Sensitivity
SENSITIVITY_PUBLIC: Sensitivity
SENSITIVITY_PRIVATE: Sensitivity
SENSITIVITY_SECRET: Sensitivity
CONVERSATION_STATE_UNSPECIFIED: ConversationState
CONVERSATION_STATE_IDLE: ConversationState
CONVERSATION_STATE_CONNECTING: ConversationState
CONVERSATION_STATE_LISTENING: ConversationState
CONVERSATION_STATE_THINKING: ConversationState
CONVERSATION_STATE_SPEAKING: ConversationState
CONVERSATION_STATE_READING: ConversationState
CONVERSATION_STATE_PAUSED: ConversationState
CONVERSATION_STATE_RECONNECTING: ConversationState
CONVERSATION_STATE_FAILED: ConversationState
PLAYBACK_STATUS_UNSPECIFIED: PlaybackStatus
PLAYBACK_STATUS_STARTED: PlaybackStatus
PLAYBACK_STATUS_POSITION: PlaybackStatus
PLAYBACK_STATUS_COMPLETED: PlaybackStatus
PLAYBACK_STATUS_FLUSHED: PlaybackStatus
PLAYBACK_STATUS_FAILED: PlaybackStatus
TOOL_OUTCOME_UNSPECIFIED: ToolOutcome
TOOL_OUTCOME_REQUESTED: ToolOutcome
TOOL_OUTCOME_AUTHORIZED: ToolOutcome
TOOL_OUTCOME_DENIED: ToolOutcome
TOOL_OUTCOME_RUNNING: ToolOutcome
TOOL_OUTCOME_SUCCEEDED: ToolOutcome
TOOL_OUTCOME_CANCELLED: ToolOutcome
TOOL_OUTCOME_FAILED: ToolOutcome
AGENT_CONTROL_SCOPE_UNSPECIFIED: AgentControlScope
AGENT_CONTROL_SCOPE_SNIPPET: AgentControlScope
AGENT_CONTROL_SCOPE_TURN: AgentControlScope
AGENT_CONTROL_SCOPE_SESSION: AgentControlScope
AGENT_CONTROL_SCOPE_DEVICE: AgentControlScope
AGENT_CONTROL_SCOPE_OPERATOR_DEFAULT: AgentControlScope
AGENT_CONTROL_ACTION_ID_UNSPECIFIED: AgentControlActionId
AGENT_CONTROL_ACTION_ID_SPEECH_DELIVERY_SET_RATE: AgentControlActionId
AGENT_CONTROL_ACTION_ID_CONFIGURATION_SET: AgentControlActionId
AGENT_CONTROL_ACTION_ID_SESSION_CREATE: AgentControlActionId
AGENT_CONTROL_ACTION_ID_SESSION_SWITCH: AgentControlActionId
AGENT_CONTROL_ACTION_ID_SESSION_RESTART: AgentControlActionId
AGENT_CONTROL_CONFIRMATION_REQUIREMENT_UNSPECIFIED: AgentControlConfirmationRequirement
AGENT_CONTROL_CONFIRMATION_REQUIREMENT_NONE: AgentControlConfirmationRequirement
AGENT_CONTROL_CONFIRMATION_REQUIREMENT_USER: AgentControlConfirmationRequirement
AGENT_CONTROL_CONFIRMATION_REQUIREMENT_OPERATOR: AgentControlConfirmationRequirement
AGENT_CONTROL_DISPOSITION_UNSPECIFIED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_REQUESTED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_AWAITING_CONFIRMATION: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_AUTHORIZED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_APPLIED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_REJECTED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_DUPLICATE: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_CANCELLED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_FAILED: AgentControlDisposition
AGENT_CONTROL_DISPOSITION_SUPERSEDED: AgentControlDisposition
AGENT_CONTROL_REJECTION_CODE_UNSPECIFIED: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_UNSUPPORTED_SCHEMA_VERSION: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_MALFORMED: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_UNREGISTERED_ACTION: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_UNAUTHORIZED: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_INVALID_SCOPE: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_INVALID_TARGET: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_INVALID_ARGUMENT: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_STALE_GENERATION: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_IDEMPOTENCY_CONFLICT: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_CONFLICT: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_CONFIRMATION_POLICY_MISMATCH: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_CANCELLED: AgentControlRejectionCode
AGENT_CONTROL_REJECTION_CODE_ALREADY_APPLIED: AgentControlRejectionCode
SPEECH_RATE_UNSPECIFIED: SpeechRate
SPEECH_RATE_SLOW_0_75X: SpeechRate
SPEECH_RATE_NORMAL_1_0X: SpeechRate
SPEECH_RATE_FAST_1_25X: SpeechRate
SPEECH_RATE_VERY_FAST_1_5X: SpeechRate
AGENT_CONFIGURATION_KEY_UNSPECIFIED: AgentConfigurationKey
AGENT_CONFIGURATION_KEY_RESPONSE_DETAIL: AgentConfigurationKey
AGENT_CONFIGURATION_KEY_DEFAULT_SPEECH_RATE: AgentConfigurationKey
RESPONSE_DETAIL_UNSPECIFIED: ResponseDetail
RESPONSE_DETAIL_BRIEF: ResponseDetail
RESPONSE_DETAIL_STANDARD: ResponseDetail
RESPONSE_DETAIL_DETAILED: ResponseDetail

class ContentRef(_message.Message):
    __slots__ = ("mime_type", "byte_length", "inline_text", "blob_uri")
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    BYTE_LENGTH_FIELD_NUMBER: _ClassVar[int]
    INLINE_TEXT_FIELD_NUMBER: _ClassVar[int]
    BLOB_URI_FIELD_NUMBER: _ClassVar[int]
    mime_type: str
    byte_length: int
    inline_text: str
    blob_uri: str
    def __init__(self, mime_type: _Optional[str] = ..., byte_length: _Optional[int] = ..., inline_text: _Optional[str] = ..., blob_uri: _Optional[str] = ...) -> None: ...

class Provenance(_message.Message):
    __slots__ = ("locator", "display_name", "extractor", "extractor_version", "source_sha256", "permission_basis")
    LOCATOR_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    EXTRACTOR_FIELD_NUMBER: _ClassVar[int]
    EXTRACTOR_VERSION_FIELD_NUMBER: _ClassVar[int]
    SOURCE_SHA256_FIELD_NUMBER: _ClassVar[int]
    PERMISSION_BASIS_FIELD_NUMBER: _ClassVar[int]
    locator: str
    display_name: str
    extractor: str
    extractor_version: str
    source_sha256: bytes
    permission_basis: str
    def __init__(self, locator: _Optional[str] = ..., display_name: _Optional[str] = ..., extractor: _Optional[str] = ..., extractor_version: _Optional[str] = ..., source_sha256: _Optional[bytes] = ..., permission_basis: _Optional[str] = ...) -> None: ...

class MessageEnvelope(_message.Message):
    __slots__ = ("schema_version", "message_id", "session_id", "sequence", "source", "content", "content_sha256", "created_at", "metadata", "provenance", "sensitivity")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_SHA256_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    SENSITIVITY_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    message_id: str
    session_id: str
    sequence: int
    source: SourceKind
    content: ContentRef
    content_sha256: bytes
    created_at: _timestamp_pb2.Timestamp
    metadata: _containers.ScalarMap[str, str]
    provenance: Provenance
    sensitivity: Sensitivity
    def __init__(self, schema_version: _Optional[int] = ..., message_id: _Optional[str] = ..., session_id: _Optional[str] = ..., sequence: _Optional[int] = ..., source: _Optional[_Union[SourceKind, str]] = ..., content: _Optional[_Union[ContentRef, _Mapping]] = ..., content_sha256: _Optional[bytes] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., metadata: _Optional[_Mapping[str, str]] = ..., provenance: _Optional[_Union[Provenance, _Mapping]] = ..., sensitivity: _Optional[_Union[Sensitivity, str]] = ...) -> None: ...

class OperationRef(_message.Message):
    __slots__ = ("session_id", "generation", "operation_id", "cancellation_id", "trace_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    CANCELLATION_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    generation: int
    operation_id: str
    cancellation_id: str
    trace_id: str
    def __init__(self, session_id: _Optional[str] = ..., generation: _Optional[int] = ..., operation_id: _Optional[str] = ..., cancellation_id: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...

class SessionStart(_message.Message):
    __slots__ = ("request_id", "session_id", "participant_identity", "last_acknowledged_sequence", "idempotency_key")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANT_IDENTITY_FIELD_NUMBER: _ClassVar[int]
    LAST_ACKNOWLEDGED_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    session_id: str
    participant_identity: str
    last_acknowledged_sequence: int
    idempotency_key: str
    def __init__(self, request_id: _Optional[str] = ..., session_id: _Optional[str] = ..., participant_identity: _Optional[str] = ..., last_acknowledged_sequence: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class SessionReady(_message.Message):
    __slots__ = ("request_id", "session_id", "generation", "last_persisted_sequence")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    LAST_PERSISTED_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    session_id: str
    generation: int
    last_persisted_sequence: int
    def __init__(self, request_id: _Optional[str] = ..., session_id: _Optional[str] = ..., generation: _Optional[int] = ..., last_persisted_sequence: _Optional[int] = ...) -> None: ...

class StateChanged(_message.Message):
    __slots__ = ("operation", "previous", "current", "reason", "occurred_at")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    operation: OperationRef
    previous: ConversationState
    current: ConversationState
    reason: str
    occurred_at: _timestamp_pb2.Timestamp
    def __init__(self, operation: _Optional[_Union[OperationRef, _Mapping]] = ..., previous: _Optional[_Union[ConversationState, str]] = ..., current: _Optional[_Union[ConversationState, str]] = ..., reason: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class PlaybackAcknowledgement(_message.Message):
    __slots__ = ("acknowledgement_id", "operation", "segment_id", "status", "position_ms", "boundary_index", "occurred_at", "detail", "playback_id")
    ACKNOWLEDGEMENT_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    POSITION_MS_FIELD_NUMBER: _ClassVar[int]
    BOUNDARY_INDEX_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    PLAYBACK_ID_FIELD_NUMBER: _ClassVar[int]
    acknowledgement_id: str
    operation: OperationRef
    segment_id: str
    status: PlaybackStatus
    position_ms: int
    boundary_index: int
    occurred_at: _timestamp_pb2.Timestamp
    detail: str
    playback_id: str
    def __init__(self, acknowledgement_id: _Optional[str] = ..., operation: _Optional[_Union[OperationRef, _Mapping]] = ..., segment_id: _Optional[str] = ..., status: _Optional[_Union[PlaybackStatus, str]] = ..., position_ms: _Optional[int] = ..., boundary_index: _Optional[int] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., detail: _Optional[str] = ..., playback_id: _Optional[str] = ...) -> None: ...

class PlaybackSegment(_message.Message):
    __slots__ = ("operation", "segment_id", "boundary_index", "duration_ms", "canonical_sha256", "source", "playback_id")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_ID_FIELD_NUMBER: _ClassVar[int]
    BOUNDARY_INDEX_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_SHA256_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    PLAYBACK_ID_FIELD_NUMBER: _ClassVar[int]
    operation: OperationRef
    segment_id: str
    boundary_index: int
    duration_ms: int
    canonical_sha256: bytes
    source: SourceKind
    playback_id: str
    def __init__(self, operation: _Optional[_Union[OperationRef, _Mapping]] = ..., segment_id: _Optional[str] = ..., boundary_index: _Optional[int] = ..., duration_ms: _Optional[int] = ..., canonical_sha256: _Optional[bytes] = ..., source: _Optional[_Union[SourceKind, str]] = ..., playback_id: _Optional[str] = ...) -> None: ...

class CapabilityGrant(_message.Message):
    __slots__ = ("grant_id", "session_id", "readable_roots", "allowed_tools", "expires_at")
    GRANT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    READABLE_ROOTS_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_TOOLS_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    grant_id: str
    session_id: str
    readable_roots: _containers.RepeatedScalarFieldContainer[str]
    allowed_tools: _containers.RepeatedScalarFieldContainer[str]
    expires_at: _timestamp_pb2.Timestamp
    def __init__(self, grant_id: _Optional[str] = ..., session_id: _Optional[str] = ..., readable_roots: _Optional[_Iterable[str]] = ..., allowed_tools: _Optional[_Iterable[str]] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ToolCall(_message.Message):
    __slots__ = ("operation", "call_id", "tool_name", "arguments_json", "capability_grant_id", "idempotency_key")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_GRANT_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    operation: OperationRef
    call_id: str
    tool_name: str
    arguments_json: bytes
    capability_grant_id: str
    idempotency_key: str
    def __init__(self, operation: _Optional[_Union[OperationRef, _Mapping]] = ..., call_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., arguments_json: _Optional[bytes] = ..., capability_grant_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ToolProgress(_message.Message):
    __slots__ = ("call_id", "outcome", "detail", "occurred_at")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    outcome: ToolOutcome
    detail: str
    occurred_at: _timestamp_pb2.Timestamp
    def __init__(self, call_id: _Optional[str] = ..., outcome: _Optional[_Union[ToolOutcome, str]] = ..., detail: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ToolResult(_message.Message):
    __slots__ = ("call_id", "outcome", "result", "error_code", "error_message")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    outcome: ToolOutcome
    result: MessageEnvelope
    error_code: str
    error_message: str
    def __init__(self, call_id: _Optional[str] = ..., outcome: _Optional[_Union[ToolOutcome, str]] = ..., result: _Optional[_Union[MessageEnvelope, _Mapping]] = ..., error_code: _Optional[str] = ..., error_message: _Optional[str] = ...) -> None: ...

class AgentControlOrigin(_message.Message):
    __slots__ = ("source", "actor_id", "source_message_id", "tool_call_id")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    source: SourceKind
    actor_id: str
    source_message_id: str
    tool_call_id: str
    def __init__(self, source: _Optional[_Union[SourceKind, str]] = ..., actor_id: _Optional[str] = ..., source_message_id: _Optional[str] = ..., tool_call_id: _Optional[str] = ...) -> None: ...

class AgentControlTarget(_message.Message):
    __slots__ = ("scope", "session_id", "device_id", "turn_id", "snippet_id")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    SNIPPET_ID_FIELD_NUMBER: _ClassVar[int]
    scope: AgentControlScope
    session_id: str
    device_id: str
    turn_id: str
    snippet_id: str
    def __init__(self, scope: _Optional[_Union[AgentControlScope, str]] = ..., session_id: _Optional[str] = ..., device_id: _Optional[str] = ..., turn_id: _Optional[str] = ..., snippet_id: _Optional[str] = ...) -> None: ...

class SpeechDeliveryAction(_message.Message):
    __slots__ = ("rate",)
    RATE_FIELD_NUMBER: _ClassVar[int]
    rate: SpeechRate
    def __init__(self, rate: _Optional[_Union[SpeechRate, str]] = ...) -> None: ...

class ConfigurationAction(_message.Message):
    __slots__ = ("key", "response_detail", "speech_rate")
    KEY_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_DETAIL_FIELD_NUMBER: _ClassVar[int]
    SPEECH_RATE_FIELD_NUMBER: _ClassVar[int]
    key: AgentConfigurationKey
    response_detail: ResponseDetail
    speech_rate: SpeechRate
    def __init__(self, key: _Optional[_Union[AgentConfigurationKey, str]] = ..., response_detail: _Optional[_Union[ResponseDetail, str]] = ..., speech_rate: _Optional[_Union[SpeechRate, str]] = ...) -> None: ...

class SessionCreateAction(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SessionSwitchAction(_message.Message):
    __slots__ = ("destination_session_id",)
    DESTINATION_SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    destination_session_id: str
    def __init__(self, destination_session_id: _Optional[str] = ...) -> None: ...

class SessionRestartAction(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class AgentControlActionRequest(_message.Message):
    __slots__ = ("schema_version", "request_id", "operation", "origin", "target", "action_id", "request_sequence", "idempotency_key", "confirmation", "speech_delivery", "configuration", "session_create", "session_switch", "session_restart", "requested_at")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    ACTION_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    CONFIRMATION_FIELD_NUMBER: _ClassVar[int]
    SPEECH_DELIVERY_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_FIELD_NUMBER: _ClassVar[int]
    SESSION_CREATE_FIELD_NUMBER: _ClassVar[int]
    SESSION_SWITCH_FIELD_NUMBER: _ClassVar[int]
    SESSION_RESTART_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_AT_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    request_id: str
    operation: OperationRef
    origin: AgentControlOrigin
    target: AgentControlTarget
    action_id: AgentControlActionId
    request_sequence: int
    idempotency_key: str
    confirmation: AgentControlConfirmationRequirement
    speech_delivery: SpeechDeliveryAction
    configuration: ConfigurationAction
    session_create: SessionCreateAction
    session_switch: SessionSwitchAction
    session_restart: SessionRestartAction
    requested_at: _timestamp_pb2.Timestamp
    def __init__(self, schema_version: _Optional[int] = ..., request_id: _Optional[str] = ..., operation: _Optional[_Union[OperationRef, _Mapping]] = ..., origin: _Optional[_Union[AgentControlOrigin, _Mapping]] = ..., target: _Optional[_Union[AgentControlTarget, _Mapping]] = ..., action_id: _Optional[_Union[AgentControlActionId, str]] = ..., request_sequence: _Optional[int] = ..., idempotency_key: _Optional[str] = ..., confirmation: _Optional[_Union[AgentControlConfirmationRequirement, str]] = ..., speech_delivery: _Optional[_Union[SpeechDeliveryAction, _Mapping]] = ..., configuration: _Optional[_Union[ConfigurationAction, _Mapping]] = ..., session_create: _Optional[_Union[SessionCreateAction, _Mapping]] = ..., session_switch: _Optional[_Union[SessionSwitchAction, _Mapping]] = ..., session_restart: _Optional[_Union[SessionRestartAction, _Mapping]] = ..., requested_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AgentControlConfirmation(_message.Message):
    __slots__ = ("schema_version", "confirmation_id", "request_id", "operation", "origin", "approved", "idempotency_key", "occurred_at")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    CONFIRMATION_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    APPROVED_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    confirmation_id: str
    request_id: str
    operation: OperationRef
    origin: AgentControlOrigin
    approved: bool
    idempotency_key: str
    occurred_at: _timestamp_pb2.Timestamp
    def __init__(self, schema_version: _Optional[int] = ..., confirmation_id: _Optional[str] = ..., request_id: _Optional[str] = ..., operation: _Optional[_Union[OperationRef, _Mapping]] = ..., origin: _Optional[_Union[AgentControlOrigin, _Mapping]] = ..., approved: _Optional[bool] = ..., idempotency_key: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AgentControlCancellation(_message.Message):
    __slots__ = ("schema_version", "cancellation_id", "request_id", "operation", "origin", "idempotency_key", "occurred_at")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    CANCELLATION_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    cancellation_id: str
    request_id: str
    operation: OperationRef
    origin: AgentControlOrigin
    idempotency_key: str
    occurred_at: _timestamp_pb2.Timestamp
    def __init__(self, schema_version: _Optional[int] = ..., cancellation_id: _Optional[str] = ..., request_id: _Optional[str] = ..., operation: _Optional[_Union[OperationRef, _Mapping]] = ..., origin: _Optional[_Union[AgentControlOrigin, _Mapping]] = ..., idempotency_key: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AgentControlActionResult(_message.Message):
    __slots__ = ("schema_version", "request_id", "operation", "action_id", "disposition", "rejection_code", "idempotency_key", "request_sha256", "state_sha256", "applied_sequence", "occurred_at", "retryable")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ACTION_ID_FIELD_NUMBER: _ClassVar[int]
    DISPOSITION_FIELD_NUMBER: _ClassVar[int]
    REJECTION_CODE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    REQUEST_SHA256_FIELD_NUMBER: _ClassVar[int]
    STATE_SHA256_FIELD_NUMBER: _ClassVar[int]
    APPLIED_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    schema_version: int
    request_id: str
    operation: OperationRef
    action_id: AgentControlActionId
    disposition: AgentControlDisposition
    rejection_code: AgentControlRejectionCode
    idempotency_key: str
    request_sha256: bytes
    state_sha256: bytes
    applied_sequence: int
    occurred_at: _timestamp_pb2.Timestamp
    retryable: bool
    def __init__(self, schema_version: _Optional[int] = ..., request_id: _Optional[str] = ..., operation: _Optional[_Union[OperationRef, _Mapping]] = ..., action_id: _Optional[_Union[AgentControlActionId, str]] = ..., disposition: _Optional[_Union[AgentControlDisposition, str]] = ..., rejection_code: _Optional[_Union[AgentControlRejectionCode, str]] = ..., idempotency_key: _Optional[str] = ..., request_sha256: _Optional[bytes] = ..., state_sha256: _Optional[bytes] = ..., applied_sequence: _Optional[int] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., retryable: _Optional[bool] = ...) -> None: ...

class Reconnect(_message.Message):
    __slots__ = ("request_id", "session_id", "generation", "last_acknowledged_sequence", "latest_playback", "idempotency_key")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    LAST_ACKNOWLEDGED_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    LATEST_PLAYBACK_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    session_id: str
    generation: int
    last_acknowledged_sequence: int
    latest_playback: _containers.RepeatedCompositeFieldContainer[PlaybackAcknowledgement]
    idempotency_key: str
    def __init__(self, request_id: _Optional[str] = ..., session_id: _Optional[str] = ..., generation: _Optional[int] = ..., last_acknowledged_sequence: _Optional[int] = ..., latest_playback: _Optional[_Iterable[_Union[PlaybackAcknowledgement, _Mapping]]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ControlCommand(_message.Message):
    __slots__ = ("command_id", "kind", "operation", "idempotency_key")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[ControlCommand.Kind]
        KIND_INTERRUPT: _ClassVar[ControlCommand.Kind]
        KIND_PAUSE: _ClassVar[ControlCommand.Kind]
        KIND_RESUME: _ClassVar[ControlCommand.Kind]
        KIND_STOP: _ClassVar[ControlCommand.Kind]
    KIND_UNSPECIFIED: ControlCommand.Kind
    KIND_INTERRUPT: ControlCommand.Kind
    KIND_PAUSE: ControlCommand.Kind
    KIND_RESUME: ControlCommand.Kind
    KIND_STOP: ControlCommand.Kind
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    kind: ControlCommand.Kind
    operation: OperationRef
    idempotency_key: str
    def __init__(self, command_id: _Optional[str] = ..., kind: _Optional[_Union[ControlCommand.Kind, str]] = ..., operation: _Optional[_Union[OperationRef, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ProtocolError(_message.Message):
    __slots__ = ("request_id", "code", "message", "retryable")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    code: str
    message: str
    retryable: bool
    def __init__(self, request_id: _Optional[str] = ..., code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...

class ClientEvent(_message.Message):
    __slots__ = ("protocol_version", "session_start", "reconnect", "playback_acknowledgement", "control_command", "message", "agent_control_confirmation", "agent_control_cancellation")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    SESSION_START_FIELD_NUMBER: _ClassVar[int]
    RECONNECT_FIELD_NUMBER: _ClassVar[int]
    PLAYBACK_ACKNOWLEDGEMENT_FIELD_NUMBER: _ClassVar[int]
    CONTROL_COMMAND_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    AGENT_CONTROL_CONFIRMATION_FIELD_NUMBER: _ClassVar[int]
    AGENT_CONTROL_CANCELLATION_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    session_start: SessionStart
    reconnect: Reconnect
    playback_acknowledgement: PlaybackAcknowledgement
    control_command: ControlCommand
    message: MessageEnvelope
    agent_control_confirmation: AgentControlConfirmation
    agent_control_cancellation: AgentControlCancellation
    def __init__(self, protocol_version: _Optional[int] = ..., session_start: _Optional[_Union[SessionStart, _Mapping]] = ..., reconnect: _Optional[_Union[Reconnect, _Mapping]] = ..., playback_acknowledgement: _Optional[_Union[PlaybackAcknowledgement, _Mapping]] = ..., control_command: _Optional[_Union[ControlCommand, _Mapping]] = ..., message: _Optional[_Union[MessageEnvelope, _Mapping]] = ..., agent_control_confirmation: _Optional[_Union[AgentControlConfirmation, _Mapping]] = ..., agent_control_cancellation: _Optional[_Union[AgentControlCancellation, _Mapping]] = ...) -> None: ...

class ServerEvent(_message.Message):
    __slots__ = ("protocol_version", "session_ready", "state_changed", "message", "tool_progress", "tool_result", "error", "playback_segment", "agent_control_request", "agent_control_result")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    SESSION_READY_FIELD_NUMBER: _ClassVar[int]
    STATE_CHANGED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TOOL_PROGRESS_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    PLAYBACK_SEGMENT_FIELD_NUMBER: _ClassVar[int]
    AGENT_CONTROL_REQUEST_FIELD_NUMBER: _ClassVar[int]
    AGENT_CONTROL_RESULT_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    session_ready: SessionReady
    state_changed: StateChanged
    message: MessageEnvelope
    tool_progress: ToolProgress
    tool_result: ToolResult
    error: ProtocolError
    playback_segment: PlaybackSegment
    agent_control_request: AgentControlActionRequest
    agent_control_result: AgentControlActionResult
    def __init__(self, protocol_version: _Optional[int] = ..., session_ready: _Optional[_Union[SessionReady, _Mapping]] = ..., state_changed: _Optional[_Union[StateChanged, _Mapping]] = ..., message: _Optional[_Union[MessageEnvelope, _Mapping]] = ..., tool_progress: _Optional[_Union[ToolProgress, _Mapping]] = ..., tool_result: _Optional[_Union[ToolResult, _Mapping]] = ..., error: _Optional[_Union[ProtocolError, _Mapping]] = ..., playback_segment: _Optional[_Union[PlaybackSegment, _Mapping]] = ..., agent_control_request: _Optional[_Union[AgentControlActionRequest, _Mapping]] = ..., agent_control_result: _Optional[_Union[AgentControlActionResult, _Mapping]] = ...) -> None: ...
