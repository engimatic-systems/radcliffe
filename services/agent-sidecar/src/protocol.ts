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
  packets: ContextPacket[];
}

export type SidecarEvent =
  | { type: "model_delta"; requestId: string; text: string }
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
  | { type: "complete"; requestId: string }
  | { type: "error"; requestId: string; message: string };

export interface ToolExecutionRequest {
  sessionId: string;
  generation: number;
  callId: string;
  idempotencyKey: string;
  toolName: "read_source";
  arguments: { path: string };
}

export interface ToolExecutionResult {
  modelContent: string;
  details: Record<string, unknown>;
}

export type ToolExecutor = (
  request: ToolExecutionRequest,
  signal?: AbortSignal,
  onUpdate?: (update: unknown) => void,
) => Promise<ToolExecutionResult>;
