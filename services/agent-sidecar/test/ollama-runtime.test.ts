import { describe, expect, it, vi } from "vitest";

import { ollamaKeepAliveValue, warmOllama } from "../src/ollama-runtime.js";

describe("Ollama runtime readiness", () => {
  it("loads the selected model and applies the configured keep-alive", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => new Response(null, { status: 200 }));

    await warmOllama({
      baseUrl: "http://127.0.0.1:11434/v1",
      modelId: "qwen3:8b",
      keepAlive: "-1",
      fetchImpl,
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, request] = fetchImpl.mock.calls[0]!;
    expect(String(url)).toBe("http://127.0.0.1:11434/api/generate");
    expect(JSON.parse(String(request?.body))).toEqual({
      model: "qwen3:8b",
      prompt: "",
      stream: false,
      keep_alive: -1,
    });
  });

  it("preserves duration keep-alives", () => {
    expect(ollamaKeepAliveValue("30m")).toBe("30m");
    expect(ollamaKeepAliveValue("0")).toBe(0);
  });

  it("fails readiness when Ollama rejects the warmup", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => new Response(null, { status: 503 }));

    await expect(
      warmOllama({
        baseUrl: "http://127.0.0.1:11434/v1",
        modelId: "qwen3:8b",
        keepAlive: "5m",
        fetchImpl,
      }),
    ).rejects.toThrow("HTTP 503");
  });
});
