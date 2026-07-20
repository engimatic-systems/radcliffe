import {
  Agent,
  type AgentMessage,
  type AgentTool,
  type StreamFn,
} from "@earendil-works/pi-agent-core";
import {
  type AssistantMessage,
  type Message,
  type Model,
  Type,
} from "@earendil-works/pi-ai";

import type { ContextMeter } from "./context-meter.js";
import type {
  ContextPacket,
  ContextUsage,
  SidecarEvent,
  ToolExecutionRequest,
  ToolExecutor,
  TurnRequest,
} from "./protocol.js";

interface RadcliffeContextMessage {
  role: "radcliffe";
  packet: ContextPacket;
}

declare module "@earendil-works/pi-agent-core" {
  interface CustomAgentMessages {
    radcliffe: RadcliffeContextMessage;
  }
}

export interface AgentOrchestrator {
  run(
    request: TurnRequest,
    emit: (event: SidecarEvent) => void | Promise<void>,
    signal?: AbortSignal,
  ): Promise<void>;
}

export const RADCLIFFE_TOOL_NAMES = [
  "list_sources",
  "read_source",
  "read_job_source",
  "configure",
  "manage_session",
  "run_job",
  "manage_job",
] as const;

type ConfigureArguments = Extract<
  ToolExecutionRequest,
  { toolName: "configure" }
>["arguments"];
type ManageSessionArguments = Extract<
  ToolExecutionRequest,
  { toolName: "manage_session" }
>["arguments"];
type RunJobArguments = Extract<ToolExecutionRequest, { toolName: "run_job" }>["arguments"];
type ManageJobArguments = Extract<
  ToolExecutionRequest,
  { toolName: "manage_job" }
>["arguments"];
export type JobManagementOperation = ManageJobArguments["operation"];

const CONFIGURATION_OPERATIONS = ["inspect", "set", "reset", "undo"] as const;
const CONFIGURATION_KEYS = ["response_detail", "default_speech_rate"] as const;
const CONFIGURATION_SCOPES = ["turn", "session"] as const;
const RESPONSE_DETAILS = ["brief", "standard", "detailed"] as const;
const SPEECH_RATES = ["0.75x", "1.0x", "1.25x", "1.5x"] as const;
const SESSION_OPERATIONS = ["create", "switch", "restart", "end"] as const;
const JOB_NOTIFICATION_POLICIES = [
  "immediate",
  "next_safe_pause",
  "silent",
  "user_waiting",
] as const;
const JOB_OPERATIONS = ["status", "follow_up", "set_priority", "cancel"] as const;
const JOB_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$/;

function stringEnum(values: readonly string[]) {
  return Type.String({ enum: [...values] });
}

function argumentRecord(value: unknown, toolName: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${toolName} arguments must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => key in value) && Object.keys(value).every((key) => allowed.has(key));
}

function normalizeOperationAlias(
  value: Record<string, unknown>,
  safeOperations: readonly string[],
): Record<string, unknown> {
  if (
    value.operation !== undefined ||
    typeof value.action !== "string" ||
    !safeOperations.includes(value.action)
  ) {
    return value;
  }
  const normalized: Record<string, unknown> = { ...value, operation: value.action };
  delete normalized.action;
  return normalized;
}

function isMember<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && values.includes(value as T);
}

export function prepareConfigureArguments(value: unknown): ConfigureArguments {
  const item = normalizeOperationAlias(argumentRecord(value, "configure"), ["inspect"]);
  if (
    !exactKeys(item, ["operation", "key"], ["scope", "value", "undoMutationId"]) ||
    !isMember(CONFIGURATION_OPERATIONS, item.operation) ||
    !isMember(CONFIGURATION_KEYS, item.key) ||
    (item.scope !== undefined && !isMember(CONFIGURATION_SCOPES, item.scope))
  ) {
    throw new Error("configure arguments do not match the registered setting contract");
  }
  if (item.operation === "set") {
    const validValue =
      item.key === "response_detail"
        ? isMember(RESPONSE_DETAILS, item.value)
        : isMember(SPEECH_RATES, item.value);
    if (!validValue || item.undoMutationId !== undefined) {
      throw new Error("configure set requires one valid value for its selected key");
    }
  } else if (item.value !== undefined) {
    throw new Error("only configure set accepts a value");
  }
  if (
    item.operation === "undo"
      ? typeof item.undoMutationId !== "string" ||
        item.undoMutationId.length < 1 ||
        item.undoMutationId.length > 128
      : item.undoMutationId !== undefined
  ) {
    throw new Error("configure undo requires exactly one valid undoMutationId");
  }
  return item as ConfigureArguments;
}

export function prepareManageSessionArguments(value: unknown): ManageSessionArguments {
  const item = normalizeOperationAlias(argumentRecord(value, "manage_session"), ["create"]);
  if (
    !exactKeys(item, ["operation"], ["reference"]) ||
    !isMember(SESSION_OPERATIONS, item.operation)
  ) {
    throw new Error("manage_session requires a valid operation");
  }
  if (
    item.operation === "switch"
      ? typeof item.reference !== "string" ||
        item.reference.length < 1 ||
        item.reference.length > 120
      : item.reference !== undefined
  ) {
    throw new Error("only manage_session switch requires a recent-conversation reference");
  }
  return item as ManageSessionArguments;
}

export function prepareRunJobArguments(value: unknown): RunJobArguments {
  const item = { ...argumentRecord(value, "run_job") };
  if (
    !exactKeys(
      item,
      ["instruction"],
      ["notificationPolicy", "priority", "budgetUsd", "maxRuntimeSeconds"],
    ) ||
    typeof item.instruction !== "string" ||
    item.instruction.length < 1 ||
    item.instruction.length > 12_000
  ) {
    throw new Error("run_job requires one bounded instruction and no unknown controls");
  }
  if (!isMember(JOB_NOTIFICATION_POLICIES, item.notificationPolicy)) {
    delete item.notificationPolicy;
  }
  if (!Number.isInteger(item.priority) || Number(item.priority) < 0 || Number(item.priority) > 4) {
    delete item.priority;
  }
  if (
    typeof item.budgetUsd !== "number" ||
    !Number.isFinite(item.budgetUsd) ||
    item.budgetUsd <= 0 ||
    item.budgetUsd > 100
  ) {
    delete item.budgetUsd;
  }
  if (
    !Number.isInteger(item.maxRuntimeSeconds) ||
    Number(item.maxRuntimeSeconds) < 1 ||
    Number(item.maxRuntimeSeconds) > 86_400
  ) {
    delete item.maxRuntimeSeconds;
  }
  return item as RunJobArguments;
}

export function prepareManageJobArguments(value: unknown): ManageJobArguments {
  const item = normalizeOperationAlias(argumentRecord(value, "manage_job"), ["status"]);
  if (
    !exactKeys(item, ["operation", "jobId"], ["instruction", "priority"]) ||
    !isMember(JOB_OPERATIONS, item.operation) ||
    typeof item.jobId !== "string" ||
    !JOB_ID.test(item.jobId)
  ) {
    throw new Error("manage_job requires a valid operation and exact job ID");
  }
  if (
    item.operation === "follow_up"
      ? typeof item.instruction !== "string" ||
        item.instruction.length < 1 ||
        item.instruction.length > 4_000
      : item.instruction !== undefined
  ) {
    throw new Error("only manage_job follow_up requires an instruction");
  }
  if (
    item.operation === "set_priority"
      ? !Number.isInteger(item.priority) || Number(item.priority) < 0 || Number(item.priority) > 4
      : item.priority !== undefined
  ) {
    throw new Error("only manage_job set_priority requires a priority from 0 through 4");
  }
  return item as ManageJobArguments;
}

const JOB_REFERENCE = String.raw`(?:background\s+)?(?:job|task|work|agent)`;

export function explicitJobManagementOperation(text: string): JobManagementOperation | undefined {
  const normalized = text.trim().toLowerCase().replace(/\s+/g, " ");
  if (!normalized) return undefined;
  const asksHowTo =
    /\b(?:how (?:can|could|do|would) i|can i|could i|tell me how|explain how)\b/.test(normalized);
  if (
    !asksHowTo &&
    (new RegExp(`\\b(?:cancel|abort|stop|terminate)\\b.{0,48}\\b(?:${JOB_REFERENCE})\\b`).test(
      normalized,
    ) ||
      new RegExp(`\\b(?:${JOB_REFERENCE})\\b.{0,48}\\b(?:cancel|abort|stop|terminate)\\b`).test(
        normalized,
      ))
  ) {
    return "cancel";
  }
  if (
    !asksHowTo &&
    /\b(?:set|change|make|raise|lower)\b.{0,48}\bpriorit(?:y|ize)\b/.test(normalized)
  ) {
    return "set_priority";
  }
  if (
    !asksHowTo &&
    (/\b(?:add|send|give)\b.{0,48}\bfollow[- ]?up\b/.test(normalized) ||
      /\btell\b.{0,16}\b(?:job|agent)\b.{0,16}\bto\b/.test(normalized))
  ) {
    return "follow_up";
  }
  if (
    /\b(?:report|check|show|give me|what(?:'s| is))\b.{0,48}\b(?:status|progress)\b/.test(
      normalized,
    ) ||
    /\b(?:status|progress)\b.{0,32}\b(?:job|task|work)\b/.test(normalized)
  ) {
    return "status";
  }
  return undefined;
}

function requiredManageJobStream(base: StreamFn): StreamFn {
  let firstRequest = true;
  return (model, context, options) => {
    if (!firstRequest) return base(model, context, options);
    firstRequest = false;
    const toolChoice =
      model.api === "anthropic-messages"
        ? { type: "tool", name: "manage_job" }
        : model.api === "openai-completions"
          ? { type: "function", function: { name: "manage_job" } }
          : undefined;
    if (!toolChoice) return base(model, context, options);
    return base(model, context, { ...options, toolChoice } as Parameters<StreamFn>[2]);
  };
}

function toolResultDetails(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const details = (value as Record<string, unknown>).details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) return undefined;
  return details as Record<string, unknown>;
}

function groundedJobResponse(
  operation: JobManagementOperation,
  succeeded: boolean,
  modelResponse: string,
  details?: Record<string, unknown>,
): string {
  if (!succeeded) {
    return operation === "status"
      ? "I couldn't check that job."
      : operation === "cancel"
        ? "I couldn't cancel that job."
        : "I couldn't update that job.";
  }
  if (modelResponse.trim()) return modelResponse;
  if (operation === "cancel") return "I requested cancellation.";
  if (operation === "follow_up") return "I added that to the job.";
  if (operation === "set_priority") return "I changed the job priority.";
  const status = typeof details?.status === "string" ? details.status : "available";
  return `The job status is ${status}.`;
}

const JOB_ACTION_RESPONSE_INSTRUCTION =
  "Describe this job tool action in one short sentence using only a few words. " +
  "Then provide any additional useful context the user requested; do not omit it. " +
  "Do not quote job identifiers or serialized job metadata.";

function runJobModelContent(): string {
  return `The background job was accepted. ${JOB_ACTION_RESPONSE_INSTRUCTION}`;
}

function manageJobModelContent(
  parameters: ManageJobArguments,
  result: { modelContent: string },
): string {
  if (parameters.operation === "status") return result.modelContent;
  const outcome =
    parameters.operation === "cancel"
      ? "The cancellation request was accepted."
      : parameters.operation === "follow_up"
        ? "The job follow-up was accepted."
        : "The priority change was accepted.";
  return `${outcome} ${JOB_ACTION_RESPONSE_INSTRUCTION}`;
}

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

export const RADCLIFFE_SYSTEM_PROMPT =
  "The voice-agent harness is called Radcliffe. You are the selected conversational model " +
  "operating inside it. You may inspect, set, reset, or undo only response_detail and " +
  "default_speech_rate with configure. Map concise or short to brief, normal to standard, " +
  "thorough or verbose to detailed, slowly to 0.75x, regular to 1.0x, quickly to 1.25x, and " +
  "very fast to 1.5x. Use turn scope for a temporary request and session scope when the user " +
  "asks the change to continue. Never invent another setting key. You may create a new " +
  "conversation, switch to a named recent conversation, restart into a fresh conversation, or " +
  "end the current conversation with manage_session. Never guess between returned choices and " +
  "never copy conversation history. You may launch long research, analysis, download, or coding " +
  "work with run_job; it returns an accepted job ID promptly while this foreground conversation " +
  "continues. Call job tools without a preamble. For each successful run_job or manage_job mutation, " +
  "first describe the job tool action " +
  "in one short sentence using only a few words. Then provide any additional useful context the user " +
  "requested; do not omit it. In that tool-action sentence, never speak or reproduce the job ID, " +
  "status, notification policy, priority, budget, runtime, revision, or serialized tool result. " +
  "Use manage_job to inspect, follow up, reprioritize, or cancel an " +
  "existing job. Never " +
  "claim a job was inspected, changed, reprioritized, followed up, or cancelled unless the matching " +
  "manage_job call succeeds in the current turn. If it fails, say that the job was not changed. Never " +
  "claim background work completed until a job event or status says so. You can discover authorized " +
  "Markdown filenames with list_sources and read " +
  "one with read_source. If the user identifies a source informally or the exact authorized path " +
  "is uncertain, call list_sources first without narrating the lookup. Use only a returned path; " +
  "if multiple paths are genuinely plausible, ask the user which one. Convert spoken filename " +
  "punctuation such as dash, hyphen, and dot M D into path characters. " +
  "Use read_job_source when the user asks to read a named source promoted by a completed " +
  "background job. Pass its human title or entity ID; an ambiguous title must be clarified, never " +
  "guessed. " +
  "Set read_source rate only when the user explicitly requests a delivery speed. " +
  "No Emojis. Do not use emoji characters in responses. " +
  "Never recreate source text.";
export const LIST_SOURCES_DESCRIPTION =
  "List the bounded catalog of relative Markdown paths authorized for this session. " +
  "Returns filenames only, never file contents or absolute host paths.";
export const LIST_SOURCES_PARAMETERS = Type.Object({}, { additionalProperties: false });
export const READ_SOURCE_DESCRIPTION =
  "Read a canonical UTF-8 Markdown source using a Radcliffe capability grant. " +
  "Normalize spoken filename punctuation before passing the path. An explicitly requested speech " +
  "rate may be slow, normal, fast, very_fast, or a numeric multiplier from 0.75 through 1.5.";
export const READ_SOURCE_PARAMETERS = Type.Object({
  path: Type.String({ minLength: 1, maxLength: 4096 }),
  rate: Type.Optional(
    Type.Union([
      Type.Literal("slow"),
      Type.Literal("normal"),
      Type.Literal("fast"),
      Type.Literal("very_fast"),
      Type.Number({ minimum: 0.75, maximum: 1.5 }),
    ]),
  ),
});
export const READ_JOB_SOURCE_DESCRIPTION =
  "Read one canonical Markdown source promoted by a completed background job. Resolve it by its " +
  "human title or exact entity ID within this conversation; ambiguous references fail closed.";
export const READ_JOB_SOURCE_PARAMETERS = Type.Object(
  {
    reference: Type.String({ minLength: 1, maxLength: 300 }),
    rate: Type.Optional(
      Type.Union([
        Type.Literal("slow"),
        Type.Literal("normal"),
        Type.Literal("fast"),
        Type.Literal("very_fast"),
        Type.Number({ minimum: 0.75, maximum: 1.5 }),
      ]),
    ),
  },
  { additionalProperties: false },
);
export const CONFIGURE_DESCRIPTION =
  "Inspect, set, reset, or undo one registered Radcliffe setting. Only response_detail and " +
  "default_speech_rate are allowed. Turn overrides expire after the current turn; session values " +
  "persist for this conversation.";
export const CONFIGURE_PARAMETERS = Type.Unsafe<ConfigureArguments>(
  Type.Object(
    {
      operation: stringEnum(CONFIGURATION_OPERATIONS),
      key: stringEnum(CONFIGURATION_KEYS),
      scope: Type.Optional(stringEnum(CONFIGURATION_SCOPES)),
      value: Type.Optional(stringEnum([...RESPONSE_DETAILS, ...SPEECH_RATES])),
      undoMutationId: Type.Optional(Type.String({ minLength: 1, maxLength: 128 })),
    },
    { additionalProperties: false },
  ),
);
export const MANAGE_SESSION_DESCRIPTION =
  "Create a fresh conversation, resolve and switch to an authorized recent conversation, " +
  "restart into a fresh conversation, or end the current conversation. Switch, restart, and " +
  "end require user confirmation. Never guess when choices are returned.";
export const MANAGE_SESSION_PARAMETERS = Type.Unsafe<ManageSessionArguments>(
  Type.Object(
    {
      operation: stringEnum(SESSION_OPERATIONS),
      reference: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
    },
    { additionalProperties: false },
  ),
);
export const RUN_JOB_DESCRIPTION =
  "Accept a bounded background research, analysis, download, or coding job without blocking the " +
  "foreground conversation. Returns a durable job ID immediately; completion arrives separately.";
export const RUN_JOB_PARAMETERS = Type.Unsafe<RunJobArguments>(
  Type.Object(
    {
      instruction: Type.String({ minLength: 1, maxLength: 12000 }),
      notificationPolicy: Type.Optional(stringEnum(JOB_NOTIFICATION_POLICIES)),
      priority: Type.Optional(Type.Integer({ minimum: 0, maximum: 4 })),
      budgetUsd: Type.Optional(Type.Number({ exclusiveMinimum: 0, maximum: 100 })),
      maxRuntimeSeconds: Type.Optional(Type.Integer({ minimum: 1, maximum: 86400 })),
    },
    { additionalProperties: false },
  ),
);
export const MANAGE_JOB_DESCRIPTION =
  "Inspect, add a private follow-up to, reprioritize, or cancel a durable background job by its " +
  "exact job ID. Job output remains separate from the foreground conversation.";
export const MANAGE_JOB_PARAMETERS = Type.Unsafe<ManageJobArguments>(
  Type.Object(
    {
      operation: stringEnum(JOB_OPERATIONS),
      jobId: Type.String({ minLength: 1, maxLength: 96, pattern: JOB_ID.source }),
      instruction: Type.Optional(Type.String({ minLength: 1, maxLength: 4000 })),
      priority: Type.Optional(Type.Integer({ minimum: 0, maximum: 4 })),
    },
    { additionalProperties: false },
  ),
);

function isRadcliffeMessage(message: AgentMessage): message is RadcliffeContextMessage {
  return "role" in message && message.role === "radcliffe";
}

function label(packet: ContextPacket): string {
  const annotations = Object.fromEntries(packet.annotations);
  const provenance = annotations.locator ? ` from ${annotations.locator}` : "";
  return `[Radcliffe ${packet.source}${provenance}]\n${packet.content}`;
}

export function convertRadcliffeContext(messages: AgentMessage[]): Message[] {
  return messages.flatMap((message): Message[] => {
    if (!isRadcliffeMessage(message)) {
      if (message.role === "user" || message.role === "assistant" || message.role === "toolResult") {
        return [message];
      }
      return [];
    }
    const packet = message.packet;
    if (!packet.include) return [];
    if (packet.role === "assistant") {
      const assistant: AssistantMessage = {
        role: "assistant",
        content: [{ type: "text", text: packet.content }],
        api: "radcliffe-context",
        provider: "radcliffe",
        model: packet.projectionVersion,
        usage: EMPTY_USAGE,
        stopReason: "stop",
        timestamp: packet.timestamp,
      };
      return [assistant];
    }
    return [{ role: "user", content: label(packet), timestamp: packet.timestamp }];
  });
}

function systemPrompt(request: TurnRequest): string {
  return (
    RADCLIFFE_SYSTEM_PROMPT +
    (request.responseDetail === "brief"
      ? " Keep this response brief."
      : request.responseDetail === "detailed"
        ? " Give a detailed response."
        : " Use standard response detail.")
  );
}

export function modelContextFromRequest(request: TurnRequest) {
  const messages: RadcliffeContextMessage[] = request.packets
    .filter((packet) => packet.include)
    .map((packet) => ({ role: "radcliffe", packet }));
  return {
    systemPrompt: systemPrompt(request),
    messages: convertRadcliffeContext(messages),
    tools: [
      {
        name: "list_sources",
        description: LIST_SOURCES_DESCRIPTION,
        parameters: LIST_SOURCES_PARAMETERS,
      },
      {
        name: "read_source",
        description: READ_SOURCE_DESCRIPTION,
        parameters: READ_SOURCE_PARAMETERS,
      },
      {
        name: "read_job_source",
        description: READ_JOB_SOURCE_DESCRIPTION,
        parameters: READ_JOB_SOURCE_PARAMETERS,
      },
      {
        name: "configure",
        description: CONFIGURE_DESCRIPTION,
        parameters: CONFIGURE_PARAMETERS,
      },
      {
        name: "manage_session",
        description: MANAGE_SESSION_DESCRIPTION,
        parameters: MANAGE_SESSION_PARAMETERS,
      },
      {
        name: "run_job",
        description: RUN_JOB_DESCRIPTION,
        parameters: RUN_JOB_PARAMETERS,
      },
      {
        name: "manage_job",
        description: MANAGE_JOB_DESCRIPTION,
        parameters: MANAGE_JOB_PARAMETERS,
      },
    ],
  };
}

export class PiAgentOrchestrator implements AgentOrchestrator {
  constructor(
    private readonly model: Model<string>,
    private readonly streamFn: StreamFn,
    private readonly executeTool: ToolExecutor,
    private readonly contextMeter?: ContextMeter,
    private readonly streamFnForTurn?: (request: TurnRequest) => StreamFn,
  ) {}

  async measure(request: TurnRequest): Promise<ContextUsage> {
    if (!this.contextMeter) throw new Error("context accounting is unavailable");
    return this.contextMeter.measure(request);
  }

  async run(
    request: TurnRequest,
    emit: (event: SidecarEvent) => void | Promise<void>,
    signal?: AbortSignal,
  ): Promise<void> {
    if (this.contextMeter) {
      const usage = await this.contextMeter.measure(request);
      if (usage.overflow) {
        throw new Error(
          `context overflow: ${usage.projectedInputTokens} input + ` +
            `${usage.reservedOutputTokens} reserved > ${usage.contextLimit} token limit`,
        );
      }
    }
    const packets = request.packets.filter((packet) => packet.include);
    if (packets.length === 0) throw new Error("turn requires an included context packet");
    const current = packets.at(-1)!;
    const jobOperation =
      current.source === "user" ? explicitJobManagementOperation(current.content) : undefined;
    const turnId =
      Object.fromEntries(current.annotations)["radcliffe.turn_id"] ?? current.messageId;
    let modelText = "";
    let groundedJobCall = false;
    let groundedJobDetails: Record<string, unknown> | undefined;
    const matchingJobCalls = new Set<string>();
    let providerError: string | undefined;
    const listSources: AgentTool<typeof LIST_SOURCES_PARAMETERS> = {
      name: "list_sources",
      label: "List sources",
      description: LIST_SOURCES_DESCRIPTION,
      parameters: LIST_SOURCES_PARAMETERS,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        _params: object,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "list_sources",
          arguments: {},
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const readSource: AgentTool<typeof READ_SOURCE_PARAMETERS> = {
      name: "read_source",
      label: "Read source",
      description: READ_SOURCE_DESCRIPTION,
      parameters: READ_SOURCE_PARAMETERS,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: {
          path: string;
          rate?: "slow" | "normal" | "fast" | "very_fast" | number;
        },
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "read_source",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const readJobSource: AgentTool<typeof READ_JOB_SOURCE_PARAMETERS> = {
      name: "read_job_source",
      label: "Read background job source",
      description: READ_JOB_SOURCE_DESCRIPTION,
      parameters: READ_JOB_SOURCE_PARAMETERS,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: {
          reference: string;
          rate?: "slow" | "normal" | "fast" | "very_fast" | number;
        },
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "read_job_source",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const configure: AgentTool<typeof CONFIGURE_PARAMETERS> = {
      name: "configure",
      label: "Configure Radcliffe",
      description: CONFIGURE_DESCRIPTION,
      parameters: CONFIGURE_PARAMETERS,
      prepareArguments: prepareConfigureArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ConfigureArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "configure",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const manageSession: AgentTool<typeof MANAGE_SESSION_PARAMETERS> = {
      name: "manage_session",
      label: "Manage conversation",
      description: MANAGE_SESSION_DESCRIPTION,
      parameters: MANAGE_SESSION_PARAMETERS,
      prepareArguments: prepareManageSessionArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ManageSessionArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "manage_session",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const runJob: AgentTool<typeof RUN_JOB_PARAMETERS> = {
      name: "run_job",
      label: "Run background job",
      description: RUN_JOB_DESCRIPTION,
      parameters: RUN_JOB_PARAMETERS,
      prepareArguments: prepareRunJobArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: RunJobArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "run_job",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: runJobModelContent() }],
          details: result.details,
        };
      },
    };
    const manageJob: AgentTool<typeof MANAGE_JOB_PARAMETERS> = {
      name: "manage_job",
      label: "Manage background job",
      description: MANAGE_JOB_DESCRIPTION,
      parameters: MANAGE_JOB_PARAMETERS,
      prepareArguments: prepareManageJobArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ManageJobArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "manage_job",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: manageJobModelContent(params, result) }],
          details: result.details,
        };
      },
    };
    const history: RadcliffeContextMessage[] = packets.slice(0, -1).map((packet) => ({
      role: "radcliffe",
      packet,
    }));
    const turnStream = this.streamFnForTurn?.(request) ?? this.streamFn;
    const agent = new Agent({
      initialState: {
        systemPrompt: systemPrompt(request),
        model: this.model,
        thinkingLevel: "off",
        tools: [
          listSources,
          readSource,
          readJobSource,
          configure,
          manageSession,
          runJob,
          manageJob,
        ],
        messages: history,
      },
      convertToLlm: convertRadcliffeContext,
      transformContext: async (messages) => messages,
      streamFn: jobOperation ? requiredManageJobStream(turnStream) : turnStream,
      sessionId: request.sessionId,
      toolExecution: "sequential",
    });
    let runJobCallSeen = false;
    let runJobSucceeded = false;
    const unsubscribe = agent.subscribe(async (event) => {
      if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
        if (
          (!runJobCallSeen || runJobSucceeded) &&
          (!jobOperation || groundedJobCall)
        ) {
          modelText += event.assistantMessageEvent.delta;
          await emit({
            type: "model_delta",
            requestId: request.requestId,
            text: event.assistantMessageEvent.delta,
          });
        }
      } else if (event.type === "message_end" && event.message.role === "assistant") {
        const complete = event.message.content
          .filter((block) => block.type === "text")
          .map((block) => block.text)
          .join("");
        if (
          complete &&
          (!runJobCallSeen || runJobSucceeded) &&
          (!jobOperation || groundedJobCall)
        ) {
          modelText = complete;
        }
        if (event.message.stopReason === "error") {
          providerError = event.message.errorMessage ?? "model provider failed";
        }
        await emit({
          type: "usage",
          requestId: request.requestId,
          provider: event.message.provider,
          modelId: event.message.responseModel ?? event.message.model,
          inputTokens: event.message.usage.input,
          outputTokens: event.message.usage.output,
          cacheReadTokens: event.message.usage.cacheRead,
          cacheWriteTokens: event.message.usage.cacheWrite,
          estimatedCostUsd: event.message.usage.cost.total,
        });
      } else if (event.type === "tool_execution_start") {
        if (event.toolName === "run_job") {
          runJobCallSeen = true;
        }
        if (
          jobOperation &&
          event.toolName === "manage_job" &&
          typeof event.args === "object" &&
          event.args !== null &&
          (event.args as Record<string, unknown>).operation === jobOperation
        ) {
          matchingJobCalls.add(event.toolCallId);
        }
        await emit({
          type: "tool_start",
          requestId: request.requestId,
          callId: event.toolCallId,
          toolName: event.toolName,
          arguments: event.args,
        });
      } else if (event.type === "tool_execution_update") {
        await emit({
          type: "tool_update",
          requestId: request.requestId,
          callId: event.toolCallId,
          toolName: event.toolName,
          update: event.partialResult,
        });
      } else if (event.type === "tool_execution_end") {
        if (event.toolName === "run_job" && !event.isError) {
          runJobSucceeded = true;
        }
        if (matchingJobCalls.has(event.toolCallId) && !event.isError) {
          groundedJobCall = true;
          groundedJobDetails = toolResultDetails(event.result);
        }
        await emit({
          type: "tool_end",
          requestId: request.requestId,
          callId: event.toolCallId,
          toolName: event.toolName,
          isError: event.isError,
          result: event.result,
        });
      }
    });
    const abort = () => agent.abort();
    signal?.addEventListener("abort", abort, { once: true });
    try {
      await agent.prompt({ role: "radcliffe", packet: current });
      if (providerError) throw new Error(providerError);
      if (runJobCallSeen) {
        if (!runJobSucceeded || !modelText.trim()) {
          modelText = runJobSucceeded ? "I started it." : "I couldn't start that job.";
          await emit({ type: "model_delta", requestId: request.requestId, text: modelText });
        }
      } else if (jobOperation) {
        const modelSuppliedResponse = Boolean(modelText.trim());
        modelText = groundedJobResponse(
          jobOperation,
          groundedJobCall,
          modelText,
          groundedJobDetails,
        );
        if (!groundedJobCall || !modelSuppliedResponse) {
          await emit({ type: "model_delta", requestId: request.requestId, text: modelText });
        }
      }
      if (modelText) {
        await emit({ type: "model_complete", requestId: request.requestId, text: modelText });
      }
      await emit({ type: "complete", requestId: request.requestId });
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown Pi failure";
      await emit({
        type: "error",
        requestId: request.requestId,
        message,
        ...(message.startsWith("cloud session budget exhausted")
          ? { code: "cloud_budget_exhausted" as const }
          : {}),
      });
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      unsubscribe();
    }
  }
}
