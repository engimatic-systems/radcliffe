import { createHash } from "node:crypto";

import type { Context, Model } from "@earendil-works/pi-ai";
import { streamSimple as streamOpenAICompletions } from "@earendil-works/pi-ai/api/openai-completions";

import { modelContextFromRequest } from "./orchestrator.js";
import type { ContextUsage, TurnRequest } from "./protocol.js";

type JsonObject = Record<string, unknown>;
type FetchLike = typeof fetch;

export interface ContextMeter {
  measure(request: TurnRequest): Promise<ContextUsage>;
}

export interface OllamaContextMeterOptions {
  model: Model<"openai-completions">;
  apiKey?: string;
  fetchImpl?: FetchLike;
  cacheEntries?: number;
}

function projectionVersion(request: TurnRequest): string {
  const versions = new Set(request.packets.map((packet) => packet.projectionVersion));
  if (versions.size > 1) throw new Error("turn mixes context projection versions");
  return versions.values().next().value ?? "context-v2";
}

export async function captureOllamaPayload(
  model: Model<"openai-completions">,
  context: Context,
  apiKey = "ollama",
): Promise<JsonObject> {
  const controller = new AbortController();
  let captured: JsonObject | undefined;
  const stream = streamOpenAICompletions(model, context, {
    apiKey,
    signal: controller.signal,
    onPayload(candidate) {
      captured = structuredClone(candidate) as JsonObject;
      controller.abort();
    },
  });
  for await (const _event of stream) {
    // The aborted provider event is expected after the exact payload is captured.
  }
  if (!captured) throw new Error("Pi did not produce an Ollama request payload");
  return captured;
}

export function accountingPayload(payload: JsonObject): JsonObject {
  const request = structuredClone(payload);
  request.stream = false;
  request.max_tokens = 1;
  delete request.max_completion_tokens;
  delete request.stream_options;
  return request;
}

export class OllamaContextMeter implements ContextMeter {
  private readonly model: Model<"openai-completions">;
  private readonly apiKey: string;
  private readonly fetchImpl: FetchLike;
  private readonly cacheEntries: number;
  private readonly cache = new Map<string, ContextUsage>();

  constructor({
    model,
    apiKey = "ollama",
    fetchImpl = fetch,
    cacheEntries = 256,
  }: OllamaContextMeterOptions) {
    if (!Number.isSafeInteger(cacheEntries) || cacheEntries <= 0) {
      throw new Error("context meter cache size must be a positive integer");
    }
    this.model = model;
    this.apiKey = apiKey;
    this.fetchImpl = fetchImpl;
    this.cacheEntries = cacheEntries;
  }

  async measure(request: TurnRequest): Promise<ContextUsage> {
    const payload = await captureOllamaPayload(
      this.model,
      modelContextFromRequest(request),
      this.apiKey,
    );
    const serialized = JSON.stringify(payload);
    const requestDigest = createHash("sha256").update(serialized).digest("hex");
    const cached = this.cache.get(requestDigest);
    if (cached) return cached;

    const response = await this.fetchImpl(
      new URL(`${this.model.baseUrl.replace(/\/$/, "")}/chat/completions`),
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${this.apiKey}`,
        },
        body: JSON.stringify(accountingPayload(payload)),
        signal: AbortSignal.timeout(120_000),
      },
    );
    if (!response.ok) {
      const detail = await response.text();
      if (
        /context (?:length|window)|max(?:imum)? context|prompt (?:is )?too long|too many tokens|token limit/i.test(
          detail,
        )
      ) {
        const usage = this.usage(
          request,
          requestDigest,
          this.model.contextWindow,
          false,
          "ollama-overflow-lower-bound-v1",
        );
        this.remember(requestDigest, usage);
        return usage;
      }
      throw new Error(`Ollama context accounting returned HTTP ${response.status}`);
    }
    const result = (await response.json()) as {
      usage?: { prompt_tokens?: number };
    };
    const projectedInputTokens = result.usage?.prompt_tokens;
    if (!Number.isSafeInteger(projectedInputTokens) || projectedInputTokens! < 0) {
      throw new Error("Ollama context accounting omitted prompt_tokens");
    }
    const truncated = projectedInputTokens! >= this.model.contextWindow - 1;
    const usage = this.usage(
      request,
      requestDigest,
      truncated ? this.model.contextWindow : projectedInputTokens!,
      !truncated,
      truncated ? "ollama-overflow-lower-bound-v1" : "ollama-openai-prompt_tokens-v1",
    );
    this.remember(requestDigest, usage);
    return usage;
  }

  private usage(
    request: TurnRequest,
    requestDigest: string,
    projectedInputTokens: number,
    exact: boolean,
    measurementBasis: string,
  ): ContextUsage {
    const contextLimit = this.model.contextWindow;
    const reservedOutputTokens = this.model.maxTokens;
    const inputCapacity = Math.max(1, contextLimit - reservedOutputTokens);
    const remainingTokens = Math.max(
      0,
      contextLimit - projectedInputTokens - reservedOutputTokens,
    );
    return {
      modelId: this.model.id,
      contextLimit,
      projectedInputTokens,
      reservedOutputTokens,
      remainingTokens,
      percentage: Math.min(100, (projectedInputTokens / inputCapacity) * 100),
      projectionVersion: projectionVersion(request),
      measurementBasis,
      exact,
      overflow: projectedInputTokens + reservedOutputTokens > contextLimit,
      requestDigest,
    };
  }

  private remember(requestDigest: string, usage: ContextUsage): void {
    if (this.cache.size >= this.cacheEntries) {
      const oldest = this.cache.keys().next().value;
      if (oldest) this.cache.delete(oldest);
    }
    this.cache.set(requestDigest, usage);
  }
}
