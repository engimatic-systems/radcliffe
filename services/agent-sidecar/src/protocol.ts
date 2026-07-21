export type SourceKind = "user" | "model" | "call" | "text_source" | "system";

export interface ContextPacket {
  projectionVersion: string;
  messageId: string;
  role: "user" | "assistant" | "tool" | "source" | "system";
  content: string;
  source: SourceKind;
  annotations: ReadonlyArray<readonly [string, string]>;
  include: boolean;
  timestamp: number;
}

export interface TurnRequest {
  requestId: string;
  sessionId: string;
  generation: number;
  operationId?: string;
  cancellationId?: string;
  traceId?: string;
  responseDetail?: ResponseDetail;
  packets: ContextPacket[];
}

const TRACE_ID = /^trc_[0-9a-f]{32}$/;

export function validTurnIdentity(turn: TurnRequest): boolean {
  return (
    typeof turn.operationId === "string" &&
    turn.operationId.length >= 16 &&
    typeof turn.cancellationId === "string" &&
    CONTROL_IDENTIFIER.test(turn.cancellationId) &&
    typeof turn.traceId === "string" &&
    TRACE_ID.test(turn.traceId)
  );
}

export interface ContextUsage {
  modelId: string;
  contextLimit: number;
  projectedInputTokens: number;
  reservedOutputTokens: number;
  remainingTokens: number;
  percentage: number;
  projectionVersion: string;
  measurementBasis: string;
  exact: boolean;
  overflow: boolean;
  requestDigest: string;
}

export type SidecarEvent =
  | {
      type: "model_delta";
      requestId: string;
      text: string;
      spokenText?: string;
      spokenCategories?: string[];
    }
  | { type: "model_complete"; requestId: string; text: string }
  | {
      type: "tool_start";
      requestId: string;
      callId: string;
      toolName: string;
      arguments: unknown;
    }
  | {
      type: "tool_update";
      requestId: string;
      callId: string;
      toolName: string;
      update: unknown;
    }
  | {
      type: "tool_end";
      requestId: string;
      callId: string;
      toolName: string;
      isError: boolean;
      result: unknown;
    }
  | {
      type: "usage";
      requestId: string;
      provider: string;
      modelId: string;
      inputTokens: number;
      outputTokens: number;
      cacheReadTokens: number;
      cacheWriteTokens: number;
      estimatedCostUsd: number;
    }
  | { type: "complete"; requestId: string }
  | {
      type: "error";
      requestId: string;
      message: string;
      code?: "cloud_budget_exhausted";
    };

interface ToolExecutionRequestBase {
  sessionId: string;
  generation: number;
  operationId: string;
  cancellationId: string;
  traceId: string;
  turnId: string;
  callId: string;
  idempotencyKey: string;
}

export type ToolExecutionRequest =
  | (ToolExecutionRequestBase & {
      toolName: "list_sources";
      arguments: Record<string, never>;
    })
  | (ToolExecutionRequestBase & {
      toolName: "read_source";
      arguments: { path: string; rate?: SpeechDeliveryRate };
    })
  | (ToolExecutionRequestBase & {
      toolName: "read_job_source";
      arguments: { reference: string; jobId?: string; rate?: SpeechDeliveryRate };
    })
  | (ToolExecutionRequestBase & {
      toolName: "configure";
      arguments:
        | {
            operation: "inspect";
            key: "response_detail" | "default_speech_rate";
            scope?: "turn" | "session";
          }
        | {
            operation: "set";
            key: "response_detail" | "default_speech_rate";
            scope?: "turn" | "session";
            value: ResponseDetail | SpeechRate;
          }
        | {
            operation: "reset";
            key: "response_detail" | "default_speech_rate";
            scope?: "turn" | "session";
          }
        | {
            operation: "undo";
            key: "response_detail" | "default_speech_rate";
            scope?: "turn" | "session";
            undoMutationId: string;
          };
    })
  | (ToolExecutionRequestBase & {
      toolName: "manage_session";
      arguments:
        | { operation: "create" | "restart" | "end" }
        | { operation: "switch"; reference: string };
    })
  | (ToolExecutionRequestBase & {
      toolName: "run_job";
      arguments: {
        instruction: string;
        notificationPolicy?: JobNotificationPolicy;
        priority?: number;
        budgetUsd?: number;
        maxRuntimeSeconds?: number;
      };
    })
  | (ToolExecutionRequestBase & {
      toolName: "manage_job";
      arguments:
        | { operation: "status"; jobId: string }
        | { operation: "follow_up"; jobId: string; instruction: string }
        | { operation: "set_priority"; jobId: string; priority: number }
        | { operation: "cancel" | "retry"; jobId: string };
    });

export interface ToolExecutionResult {
  modelContent: string;
  details: Record<string, unknown>;
}

export type ToolExecutor = (
  request: ToolExecutionRequest,
  signal?: AbortSignal,
  onUpdate?: (update: unknown) => void,
) => Promise<ToolExecutionResult>;

export const AGENT_CONTROL_SCHEMA_VERSION = 1 as const;

export const AGENT_CONTROL_ACTION_WIRE_IDS = {
  "speech_delivery.set_rate": 1,
  "configuration.set": 2,
  "session.create": 3,
  "session.switch": 4,
  "session.restart": 5,
  "configuration.inspect": 6,
  "configuration.reset": 7,
  "configuration.undo": 8,
  "session.end": 9,
} as const;

export type AgentControlActionId = keyof typeof AGENT_CONTROL_ACTION_WIRE_IDS;
export type AgentControlScope = "snippet" | "turn" | "session" | "device" | "operator_default";
export type AgentControlConfirmation = "none" | "user" | "operator";
export type SpeechRate = "0.75x" | "1.0x" | "1.25x" | "1.5x";
export type SpeechDeliveryRate = SpeechRate | "slow" | "normal" | "fast" | "very_fast" | number;
export type ResponseDetail = "brief" | "standard" | "detailed";
export type JobNotificationPolicy =
  | "immediate"
  | "next_safe_pause"
  | "silent"
  | "user_waiting";

export interface AgentControlOrigin {
  source: SourceKind;
  actorId: string;
  sourceMessageId: string;
  toolCallId?: string;
}

export interface AgentControlTarget {
  scope: AgentControlScope;
  sessionId: string;
  deviceId?: string;
  turnId?: string;
  snippetId?: string;
}

interface AgentControlRequestBase {
  schemaVersion: typeof AGENT_CONTROL_SCHEMA_VERSION;
  requestId: string;
  sessionId: string;
  generation: number;
  operationId: string;
  cancellationId: string;
  traceId: string;
  requestSequence: number;
  idempotencyKey: string;
  origin: AgentControlOrigin;
  target: AgentControlTarget;
  confirmation: AgentControlConfirmation;
}

export type AgentControlRequest =
  | (AgentControlRequestBase & {
      actionId: "speech_delivery.set_rate";
      arguments: { rate: SpeechRate | number };
    })
  | (AgentControlRequestBase & {
      actionId: "configuration.set";
      arguments:
        | { key: "response_detail"; value: ResponseDetail }
        | { key: "default_speech_rate"; value: SpeechRate };
    })
  | (AgentControlRequestBase & {
      actionId: "configuration.inspect";
      arguments: { key: "response_detail" | "default_speech_rate" };
    })
  | (AgentControlRequestBase & {
      actionId: "configuration.reset";
      arguments: { key: "response_detail" | "default_speech_rate" };
    })
  | (AgentControlRequestBase & {
      actionId: "configuration.undo";
      arguments: {
        key: "response_detail" | "default_speech_rate";
        mutationId: string;
      };
    })
  | (AgentControlRequestBase & {
      actionId: "session.create";
      arguments: Record<string, never>;
    })
  | (AgentControlRequestBase & {
      actionId: "session.switch";
      arguments: { destinationSessionId: string };
    })
  | (AgentControlRequestBase & {
      actionId: "session.restart";
      arguments: Record<string, never>;
    })
  | (AgentControlRequestBase & {
      actionId: "session.end";
      arguments: Record<string, never>;
    });

const CONTROL_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const CONTROL_TRACE_ID = /^trc_[0-9a-f]{32}$/;
const CONTROL_SOURCES = new Set<SourceKind>([
  "user",
  "model",
  "call",
  "text_source",
  "system",
]);
const CONTROL_SCOPES = new Set<AgentControlScope>([
  "snippet",
  "turn",
  "session",
  "device",
  "operator_default",
]);
const SPEECH_RATES = new Set<SpeechRate>(["0.75x", "1.0x", "1.25x", "1.5x"]);
const RESPONSE_DETAILS = new Set<ResponseDetail>(["brief", "standard", "detailed"]);
const REQUEST_KEYS = new Set([
  "schemaVersion",
  "requestId",
  "sessionId",
  "generation",
  "operationId",
  "cancellationId",
  "traceId",
  "requestSequence",
  "idempotencyKey",
  "origin",
  "target",
  "confirmation",
  "actionId",
  "arguments",
]);

function record(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, required: string[], optional: string[] = []) {
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => key in value) && Object.keys(value).every((key) => allowed.has(key));
}

function identifier(value: unknown): value is string {
  return typeof value === "string" && CONTROL_IDENTIFIER.test(value);
}

function validOrigin(value: unknown): value is AgentControlOrigin {
  const item = record(value);
  if (!item || !exactKeys(item, ["source", "actorId", "sourceMessageId"], ["toolCallId"])) {
    return false;
  }
  if (
    typeof item.source !== "string" ||
    !CONTROL_SOURCES.has(item.source as SourceKind) ||
    !identifier(item.actorId) ||
    !identifier(item.sourceMessageId)
  ) {
    return false;
  }
  return item.source === "call" ? identifier(item.toolCallId) : item.toolCallId === undefined;
}

function validTarget(value: unknown): value is AgentControlTarget {
  const item = record(value);
  if (
    !item ||
    !exactKeys(item, ["scope", "sessionId"], ["deviceId", "turnId", "snippetId"]) ||
    typeof item.scope !== "string" ||
    !CONTROL_SCOPES.has(item.scope as AgentControlScope) ||
    !identifier(item.sessionId)
  ) {
    return false;
  }
  return [item.deviceId, item.turnId, item.snippetId].every(
    (candidate) => candidate === undefined || identifier(candidate),
  );
}

function requiredConfirmation(actionId: AgentControlActionId, scope: AgentControlScope) {
  if (actionId === "speech_delivery.set_rate") return scope === "snippet" ? "none" : undefined;
  if (actionId === "configuration.inspect") {
    return scope === "turn" || scope === "session" || scope === "device" || scope === "operator_default"
      ? "none"
      : undefined;
  }
  if (
    actionId === "configuration.set" ||
    actionId === "configuration.reset" ||
    actionId === "configuration.undo"
  ) {
    if (scope === "turn" || scope === "session") return "none";
    if (scope === "device") return "user";
    if (scope === "operator_default") return "operator";
    return undefined;
  }
  if (actionId === "session.create") return scope === "session" ? "none" : undefined;
  if (
    actionId === "session.switch" ||
    actionId === "session.restart" ||
    actionId === "session.end"
  ) {
    return scope === "session" ? "user" : undefined;
  }
  return undefined;
}

function validArguments(
  actionId: AgentControlActionId,
  value: unknown,
  currentSessionId: string,
): boolean {
  const args = record(value);
  if (!args) return false;
  if (actionId === "speech_delivery.set_rate") {
    const rate = args.rate;
    return (
      exactKeys(args, ["rate"]) &&
      ((typeof rate === "string" && SPEECH_RATES.has(rate as SpeechRate)) ||
        (typeof rate === "number" && Number.isFinite(rate) && rate >= 0.75 && rate <= 1.5))
    );
  }
  if (actionId === "configuration.set") {
    if (!exactKeys(args, ["key", "value"])) return false;
    if (args.key === "response_detail") {
      return typeof args.value === "string" && RESPONSE_DETAILS.has(args.value as ResponseDetail);
    }
    if (args.key === "default_speech_rate") {
      return typeof args.value === "string" && SPEECH_RATES.has(args.value as SpeechRate);
    }
    return false;
  }
  if (actionId === "configuration.inspect" || actionId === "configuration.reset") {
    return (
      exactKeys(args, ["key"]) &&
      (args.key === "response_detail" || args.key === "default_speech_rate")
    );
  }
  if (actionId === "configuration.undo") {
    return (
      exactKeys(args, ["key", "mutationId"]) &&
      (args.key === "response_detail" || args.key === "default_speech_rate") &&
      identifier(args.mutationId)
    );
  }
  if (actionId === "session.switch") {
    return (
      exactKeys(args, ["destinationSessionId"]) &&
      identifier(args.destinationSessionId) &&
      args.destinationSessionId !== currentSessionId
    );
  }
  return exactKeys(args, []);
}

export function isAgentControlRequest(value: unknown): value is AgentControlRequest {
  const item = record(value);
  if (!item || Object.keys(item).some((key) => !REQUEST_KEYS.has(key))) return false;
  if (
    item.schemaVersion !== AGENT_CONTROL_SCHEMA_VERSION ||
    !identifier(item.requestId) ||
    !identifier(item.sessionId) ||
    !Number.isSafeInteger(item.generation) ||
    Number(item.generation) < 1 ||
    !identifier(item.operationId) ||
    !identifier(item.cancellationId) ||
    typeof item.traceId !== "string" ||
    !CONTROL_TRACE_ID.test(item.traceId) ||
    !Number.isSafeInteger(item.requestSequence) ||
    Number(item.requestSequence) < 1 ||
    !identifier(item.idempotencyKey) ||
    !validOrigin(item.origin) ||
    !validTarget(item.target) ||
    typeof item.actionId !== "string" ||
    !(item.actionId in AGENT_CONTROL_ACTION_WIRE_IDS)
  ) {
    return false;
  }
  const target = item.target;
  const actionId = item.actionId as AgentControlActionId;
  if (target.sessionId !== item.sessionId) return false;
  if (requiredConfirmation(actionId, target.scope) !== item.confirmation) return false;
  if (actionId === "speech_delivery.set_rate") {
    if (!target.turnId || !target.snippetId || target.deviceId) return false;
  } else if (
    actionId === "configuration.set" ||
    actionId === "configuration.inspect" ||
    actionId === "configuration.reset" ||
    actionId === "configuration.undo"
  ) {
    if (target.scope === "turn" && (!target.turnId || target.deviceId || target.snippetId)) {
      return false;
    }
    if (target.scope === "device" && (!target.deviceId || target.turnId || target.snippetId)) {
      return false;
    }
    if (
      target.scope !== "turn" &&
      target.scope !== "device" &&
      (target.deviceId || target.turnId || target.snippetId)
    ) {
      return false;
    }
  } else if (target.deviceId || target.turnId || target.snippetId) {
    return false;
  }
  return validArguments(actionId, item.arguments, item.sessionId as string);
}
