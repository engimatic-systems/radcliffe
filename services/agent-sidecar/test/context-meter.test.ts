import { describe, expect, it, vi } from "vitest";

import {
  accountingPayload,
  captureOllamaPayload,
  OllamaContextMeter,
} from "../src/context-meter.js";
import { createOllamaModel } from "../src/ollama-model.js";
import { modelContextFromRequest } from "../src/orchestrator.js";
import type { ContextPacket, TurnRequest } from "../src/protocol.js";

function packet(
  messageId: string,
  content: string,
  options: Partial<ContextPacket> = {},
): ContextPacket {
  return {
    projectionVersion: "context-v2",
    messageId,
    role: "user",
    content,
    source: "user",
    annotations: [["projection", "context-v2"]],
    include: true,
    timestamp: 1,
    ...options,
  };
}

function request(packets: ContextPacket[]): TurnRequest {
  return { requestId: "usage-1", sessionId: "session-1", generation: 1, packets };
}

function model(modelId = "qwen3:8b") {
  return createOllamaModel({
    modelId,
    baseUrl: "http://127.0.0.1:11434/v1",
    contextWindow: 100,
    maxTokens: 20,
  });
}

describe("Ollama context accounting", () => {
  it("captures the exact Pi payload used by the model request", async () => {
    const turn = request([
      packet("old-model", "Earlier answer", { role: "assistant", source: "model" }),
      packet("excluded", "Never send this", { include: false }),
      packet("call", '{"tool":"read_source"}', { role: "tool", source: "call" }),
      packet("source", "Canonical source", {
        role: "source",
        source: "text_source",
        annotations: [["locator", "demo.md"]],
      }),
      packet("current", "Read demo.md"),
    ]);

    const payload = await captureOllamaPayload(model(), modelContextFromRequest(turn));

    expect(payload).toMatchObject({
      model: "qwen3:8b",
      stream: true,
      max_completion_tokens: 1,
      reasoning_effort: "none",
      messages: [
        { role: "developer" },
        { role: "assistant", content: "Earlier answer" },
        { role: "user", content: '[Radcliffe call]\n{"tool":"read_source"}' },
        {
          role: "user",
          content: "[Radcliffe text_source from demo.md]\nCanonical source",
        },
        { role: "user", content: "[Radcliffe user]\nRead demo.md" },
      ],
      tools: [
        {
          type: "function",
          function: {
            name: "list_sources",
            parameters: {
              type: "object",
              properties: {},
              additionalProperties: false,
            },
          },
        },
        {
          type: "function",
          function: {
            name: "read_source",
            parameters: {
              type: "object",
              required: ["path"],
            },
          },
        },
        {
          type: "function",
          function: {
            name: "configure",
          },
        },
      ],
    });
    expect(JSON.stringify(payload)).not.toContain("Never send this");
  });

  it("uses authoritative prompt tokens, reserves output, and caches by payload digest", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      Response.json({ usage: { prompt_tokens: 30 } }),
    );
    const meter = new OllamaContextMeter({ model: model(), fetchImpl });
    const turn = request([packet("current", "Hello")]);

    const first = await meter.measure(turn);
    const second = await meter.measure(turn);

    expect(first).toEqual(second);
    expect(first).toMatchObject({
      modelId: "qwen3:8b",
      contextLimit: 100,
      projectedInputTokens: 30,
      reservedOutputTokens: 20,
      remainingTokens: 50,
      percentage: 37.5,
      projectionVersion: "context-v2",
      measurementBasis: "ollama-openai-prompt_tokens-v1",
      exact: true,
      overflow: false,
    });
    expect(first.requestDigest).toMatch(/^[a-f0-9]{64}$/);
    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(String(url)).toBe("http://127.0.0.1:11434/v1/chat/completions");
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual(accountingPayload(await captureOllamaPayload(model(), modelContextFromRequest(turn))));
    expect(body).toMatchObject({ stream: false, max_tokens: 1 });
    expect(body).not.toHaveProperty("max_completion_tokens");
    expect(body).not.toHaveProperty("stream_options");
  });

  it("flags overflow before generation and changes the digest with the model", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      Response.json({ usage: { prompt_tokens: 85 } }),
    );
    const turn = request([packet("current", "Hello")]);
    const first = await new OllamaContextMeter({ model: model(), fetchImpl }).measure(turn);
    const changed = await new OllamaContextMeter({
      model: model("qwen3:alternate"),
      fetchImpl,
    }).measure(turn);

    expect(first.overflow).toBe(true);
    expect(first.remainingTokens).toBe(0);
    expect(first.percentage).toBe(100);
    expect(changed.modelId).toBe("qwen3:alternate");
    expect(changed.requestDigest).not.toBe(first.requestDigest);
  });

  it("rejects a provider response without exact prompt usage", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => Response.json({ usage: {} }));
    const meter = new OllamaContextMeter({ model: model(), fetchImpl });

    await expect(meter.measure(request([packet("current", "Hello")]))).rejects.toThrow(
      "prompt_tokens",
    );
  });

  it("labels provider truncation and explicit context rejection as lower bounds", async () => {
    const truncatedFetch = vi.fn<typeof fetch>(async () =>
      Response.json({ usage: { prompt_tokens: 100 } }),
    );
    const rejectedFetch = vi.fn<typeof fetch>(async () =>
      new Response("prompt too long; exceeded max context length", { status: 400 }),
    );
    const turn = request([packet("current", "Hello")]);

    const truncated = await new OllamaContextMeter({
      model: model(),
      fetchImpl: truncatedFetch,
    }).measure(turn);
    const rejected = await new OllamaContextMeter({
      model: model(),
      fetchImpl: rejectedFetch,
    }).measure(turn);

    for (const usage of [truncated, rejected]) {
      expect(usage).toMatchObject({
        projectedInputTokens: 100,
        exact: false,
        overflow: true,
        measurementBasis: "ollama-overflow-lower-bound-v1",
      });
    }
  });
});
