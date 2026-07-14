import type { Model } from "@earendil-works/pi-ai";

export interface OllamaModelOptions {
  modelId: string;
  baseUrl: string;
}

/**
 * Describe Qwen's reasoning capability even though Radcliffe runs it with
 * thinking disabled. Pi only emits the provider's wire-level off value when
 * the model is reasoning-capable; without this metadata Ollama lets Qwen use
 * its default hidden-thinking mode.
 */
export function createOllamaModel({ modelId, baseUrl }: OllamaModelOptions): Model<"openai-completions"> {
  return {
    id: modelId,
    name: modelId,
    api: "openai-completions",
    provider: "ollama",
    baseUrl,
    reasoning: true,
    thinkingLevelMap: { off: "none" },
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 65536,
    maxTokens: 8192,
  };
}
