import { streamSimple as streamAnthropic } from "@earendil-works/pi-ai/api/anthropic-messages";
import { streamSimple as streamOpenAI } from "@earendil-works/pi-ai/api/openai-completions";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { hasApi } from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";

import { createOllamaModel } from "../src/ollama-model.js";
import { modelContextFromRequest, RADCLIFFE_TOOL_NAMES } from "../src/orchestrator.js";
import type { TurnRequest } from "../src/protocol.js";

function request(): TurnRequest {
  return {
    requestId: "schema-request",
    sessionId: "schema-session",
    generation: 1,
    operationId: "schema-operation-1",
    cancellationId: "schema-cancellation-1",
    traceId: `trc_${"d".repeat(32)}`,
    packets: [
      {
        projectionVersion: "context-v2",
        messageId: "schema-message",
        role: "user",
        content: "Inspect the current background job.",
        source: "user",
        annotations: [["projection", "context-v2"]],
        include: true,
        timestamp: 1,
      },
    ],
  };
}

async function drain(stream: AsyncIterable<unknown>): Promise<void> {
  for await (const _event of stream) {
    // The deliberately unreachable local endpoint lets the test inspect the payload without a call.
  }
}

describe("model tool schema adapters", () => {
  it("sends every complete object schema through the Anthropic adapter", async () => {
    const selected = builtinModels().getModel("anthropic", "claude-haiku-4-5-20251001");
    if (!selected || !hasApi(selected, "anthropic-messages")) {
      throw new Error("pinned Anthropic model is unavailable");
    }
    const context = modelContextFromRequest(request());
    let payload: unknown;
    await drain(
      streamAnthropic(
        { ...selected, baseUrl: "http://127.0.0.1:1" },
        context,
        {
          apiKey: "fake-anthropic-key",
          maxRetries: 0,
          timeoutMs: 100,
          onPayload: (value) => {
            payload = value;
          },
        },
      ),
    );

    const tools = (payload as { tools: Array<Record<string, unknown>> }).tools;
    expect(tools.map((tool) => tool.name)).toEqual(RADCLIFFE_TOOL_NAMES);
    for (const tool of tools) {
      const schema = tool.input_schema as {
        type: string;
        properties: Record<string, unknown>;
        required: string[];
      };
      expect(schema.type).toBe("object");
      expect(Object.keys(schema.properties).length).toBeGreaterThanOrEqual(0);
      if (tool.name !== "list_sources") {
        expect(Object.keys(schema.properties).length).toBeGreaterThan(0);
      }
    }
    const manageJob = tools.find((tool) => tool.name === "manage_job");
    expect(manageJob?.input_schema).toMatchObject({
      required: ["operation", "jobId"],
      properties: {
        operation: {
          type: "string",
          enum: ["status", "follow_up", "set_priority", "cancel", "retry"],
        },
      },
    });
  });

  it("sends the same complete schemas through the Ollama OpenAI adapter", async () => {
    const context = modelContextFromRequest(request());
    let payload: unknown;
    await drain(
      streamOpenAI(
        createOllamaModel({
          modelId: "qwen3:8b",
          baseUrl: "http://127.0.0.1:1/v1",
        }),
        context,
        {
          apiKey: "ollama",
          maxRetries: 0,
          timeoutMs: 100,
          onPayload: (value) => {
            payload = value;
          },
        },
      ),
    );

    const tools = (
      payload as {
        tools: Array<{ function: { name: string; parameters: Record<string, unknown> } }>;
      }
    ).tools;
    expect(tools.map((tool) => tool.function.name)).toEqual(RADCLIFFE_TOOL_NAMES);
    const manageSession = tools.find((tool) => tool.function.name === "manage_session");
    expect(manageSession?.function.parameters).toMatchObject({
      type: "object",
      required: ["operation"],
      properties: {
        operation: {
          type: "string",
          enum: ["create", "switch", "restart", "end"],
        },
      },
    });
  });
});
