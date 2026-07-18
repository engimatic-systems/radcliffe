import { streamSimple as streamOpenAICompletions } from "@earendil-works/pi-ai/api/openai-completions";
import { describe, expect, it } from "vitest";

import { createOllamaModel } from "../src/ollama-model.js";

describe("local Ollama model", () => {
  it("sends Qwen's wire-level thinking-off value", async () => {
    const model = createOllamaModel({
      modelId: "qwen3:8b",
      baseUrl: "http://127.0.0.1:1/v1",
    });
    let payload: unknown;
    const stream = streamOpenAICompletions(
      model,
      {
        messages: [
          {
            role: "user",
            content: "Reply with one word.",
            timestamp: Date.now(),
          },
        ],
      },
      {
        apiKey: "ollama",
        onPayload: (value) => {
          payload = value;
        },
      },
    );

    for await (const _event of stream) {
      // The deliberately unreachable endpoint lets us inspect the request only.
    }

    expect(payload).toMatchObject({ reasoning_effort: "none" });
  });
});
