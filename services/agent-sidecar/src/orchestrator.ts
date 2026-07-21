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

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

export const RADCLIFFE_SYSTEM_PROMPT =
  "You are Radcliffe. You may inspect, set, reset, or undo only response_detail and " +
  "default_speech_rate with configure. Map concise or short to brief, normal to standard, " +
  "thorough or verbose to detailed, slowly to 0.75x, regular to 1.0x, quickly to 1.25x, and " +
  "very fast to 1.5x. Use turn scope for a temporary request and session scope when the user " +
  "asks the change to continue. Never invent another setting key. You may create a new " +
  "conversation, switch to a named recent conversation, restart into a fresh conversation, or " +
  "end the current conversation with manage_session. Never guess between returned choices and " +
  "never copy conversation history. You can discover authorized " +
  "Markdown filenames with list_sources and read " +
  "one with read_source. If the user identifies a source informally or the exact authorized path " +
  "is uncertain, call list_sources first without narrating the lookup. Use only a returned path; " +
  "if multiple paths are genuinely plausible, ask the user which one. Convert spoken filename " +
  "punctuation such as dash, hyphen, and dot M D into path characters. " +
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
export const CONFIGURE_DESCRIPTION =
  "Inspect, set, reset, or undo one registered Radcliffe setting. Only response_detail and " +
  "default_speech_rate are allowed. Turn overrides expire after the current turn; session values " +
  "persist for this conversation.";
const CONFIGURATION_KEY = Type.Union([
  Type.Literal("response_detail"),
  Type.Literal("default_speech_rate"),
]);
const CONFIGURATION_SCOPE = Type.Optional(
  Type.Union([Type.Literal("turn"), Type.Literal("session")]),
);
export const CONFIGURE_PARAMETERS = Type.Union([
  Type.Object(
    {
      operation: Type.Literal("inspect"),
      key: CONFIGURATION_KEY,
      scope: CONFIGURATION_SCOPE,
    },
    { additionalProperties: false },
  ),
  Type.Object(
    {
      operation: Type.Literal("set"),
      key: Type.Literal("response_detail"),
      scope: CONFIGURATION_SCOPE,
      value: Type.Union([
        Type.Literal("brief"),
        Type.Literal("standard"),
        Type.Literal("detailed"),
      ]),
    },
    { additionalProperties: false },
  ),
  Type.Object(
    {
      operation: Type.Literal("set"),
      key: Type.Literal("default_speech_rate"),
      scope: CONFIGURATION_SCOPE,
      value: Type.Union([
        Type.Literal("0.75x"),
        Type.Literal("1.0x"),
        Type.Literal("1.25x"),
        Type.Literal("1.5x"),
      ]),
    },
    { additionalProperties: false },
  ),
  Type.Object(
    {
      operation: Type.Literal("reset"),
      key: CONFIGURATION_KEY,
      scope: CONFIGURATION_SCOPE,
    },
    { additionalProperties: false },
  ),
  Type.Object(
    {
      operation: Type.Literal("undo"),
      key: CONFIGURATION_KEY,
      scope: CONFIGURATION_SCOPE,
      undoMutationId: Type.String({ minLength: 1, maxLength: 128 }),
    },
    { additionalProperties: false },
  ),
]);
export const MANAGE_SESSION_DESCRIPTION =
  "Create a fresh conversation, resolve and switch to an authorized recent conversation, " +
  "restart into a fresh conversation, or end the current conversation. Switch, restart, and " +
  "end require user confirmation. Never guess when choices are returned.";
export const MANAGE_SESSION_PARAMETERS = Type.Union([
  Type.Object(
    { operation: Type.Union([Type.Literal("create"), Type.Literal("restart"), Type.Literal("end")]) },
    { additionalProperties: false },
  ),
  Type.Object(
    {
      operation: Type.Literal("switch"),
      reference: Type.String({ minLength: 1, maxLength: 120 }),
    },
    { additionalProperties: false },
  ),
]);

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
        name: "configure",
        description: CONFIGURE_DESCRIPTION,
        parameters: CONFIGURE_PARAMETERS,
      },
      {
        name: "manage_session",
        description: MANAGE_SESSION_DESCRIPTION,
        parameters: MANAGE_SESSION_PARAMETERS,
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
    const turnId =
      Object.fromEntries(current.annotations)["radcliffe.turn_id"] ?? current.messageId;
    let modelText = "";
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
    type ConfigureArguments = Extract<
      ToolExecutionRequest,
      { toolName: "configure" }
    >["arguments"];
    const configure: AgentTool<typeof CONFIGURE_PARAMETERS> = {
      name: "configure",
      label: "Configure Radcliffe",
      description: CONFIGURE_DESCRIPTION,
      parameters: CONFIGURE_PARAMETERS,
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
    type ManageSessionArguments = Extract<
      ToolExecutionRequest,
      { toolName: "manage_session" }
    >["arguments"];
    const manageSession: AgentTool<typeof MANAGE_SESSION_PARAMETERS> = {
      name: "manage_session",
      label: "Manage conversation",
      description: MANAGE_SESSION_DESCRIPTION,
      parameters: MANAGE_SESSION_PARAMETERS,
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
    const history: RadcliffeContextMessage[] = packets.slice(0, -1).map((packet) => ({
      role: "radcliffe",
      packet,
    }));
    const agent = new Agent({
      initialState: {
        systemPrompt: systemPrompt(request),
        model: this.model,
        thinkingLevel: "off",
        tools: [listSources, readSource, configure, manageSession],
        messages: history,
      },
      convertToLlm: convertRadcliffeContext,
      transformContext: async (messages) => messages,
      streamFn: this.streamFnForTurn?.(request) ?? this.streamFn,
      sessionId: request.sessionId,
      toolExecution: "sequential",
    });
    const unsubscribe = agent.subscribe(async (event) => {
      if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
        modelText += event.assistantMessageEvent.delta;
        await emit({
          type: "model_delta",
          requestId: request.requestId,
          text: event.assistantMessageEvent.delta,
        });
      } else if (event.type === "message_end" && event.message.role === "assistant") {
        const complete = event.message.content
          .filter((block) => block.type === "text")
          .map((block) => block.text)
          .join("");
        if (complete) modelText = complete;
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
