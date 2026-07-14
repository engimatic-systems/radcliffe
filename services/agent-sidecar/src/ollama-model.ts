import type { Model } from "@earendil-works/pi-ai";

export interface OllamaModelOptions {
  modelId: string;
  baseUrl: string;
  contextWindow?: number;
  maxTokens?: number;
}

/**
 * Describe Qwen's reasoning capability even though Radcliffe runs it with
 * thinking disabled. Pi only emits the provider's wire-level off value when
 * the model is reasoning-capable; without this metadata Ollama lets Qwen use
 * its default hidden-thinking mode.
 */
export function createOllamaModel({
  modelId,
  baseUrl,
  contextWindow = 40960,
  maxTokens = 8192,
}: OllamaModelOptions): Model<"openai-completions"> {
  if (!Number.isSafeInteger(contextWindow) || contextWindow <= 0) {
    throw new Error("Ollama context window must be a positive integer");
  }
  if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0 || maxTokens >= contextWindow) {
    throw new Error("Ollama reserved output must be positive and smaller than its context window");
  }
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
    contextWindow,
    maxTokens,
  };
}
