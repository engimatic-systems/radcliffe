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
  "You are Radcliffe. Use read_source for requests to read a file. " +
  "Convert spoken filename punctuation such as dash, hyphen, and dot M D into path characters. " +
  "Never recreate source text.";
export const READ_SOURCE_DESCRIPTION =
  "Read a canonical UTF-8 Markdown source using a Radcliffe capability grant. " +
  "Normalize spoken filename punctuation before passing the path.";
export const READ_SOURCE_PARAMETERS = Type.Object({
  path: Type.String({ minLength: 1, maxLength: 4096 }),
});

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

export function modelContextFromRequest(request: TurnRequest) {
  const messages: RadcliffeContextMessage[] = request.packets
    .filter((packet) => packet.include)
    .map((packet) => ({ role: "radcliffe", packet }));
  return {
    systemPrompt: RADCLIFFE_SYSTEM_PROMPT,
    messages: convertRadcliffeContext(messages),
    tools: [
      {
        name: "read_source",
        description: READ_SOURCE_DESCRIPTION,
        parameters: READ_SOURCE_PARAMETERS,
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
    let modelText = "";
    const readSource: AgentTool<typeof READ_SOURCE_PARAMETERS> = {
      name: "read_source",
      label: "Read source",
      description: READ_SOURCE_DESCRIPTION,
      parameters: READ_SOURCE_PARAMETERS,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: { path: string },
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
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
    const history: RadcliffeContextMessage[] = packets.slice(0, -1).map((packet) => ({
      role: "radcliffe",
      packet,
    }));
    const agent = new Agent({
      initialState: {
        systemPrompt: RADCLIFFE_SYSTEM_PROMPT,
        model: this.model,
        thinkingLevel: "off",
        tools: [readSource],
        messages: history,
      },
      convertToLlm: convertRadcliffeContext,
      transformContext: async (messages) => messages,
      streamFn: this.streamFn,
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
      if (modelText) {
        await emit({ type: "model_complete", requestId: request.requestId, text: modelText });
      }
      await emit({ type: "complete", requestId: request.requestId });
    } catch (error) {
      await emit({
        type: "error",
        requestId: request.requestId,
        message: error instanceof Error ? error.message : "unknown Pi failure",
      });
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      unsubscribe();
    }
  }
}
