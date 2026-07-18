export type FetchLike = typeof fetch;

export interface OllamaWarmOptions {
  baseUrl: string;
  modelId: string;
  keepAlive: string;
  fetchImpl?: FetchLike;
}

export function ollamaKeepAliveValue(value: string): string | number {
  return /^-?\d+$/.test(value) ? Number(value) : value;
}

export async function warmOllama({
  baseUrl,
  modelId,
  keepAlive,
  fetchImpl = fetch,
}: OllamaWarmOptions): Promise<void> {
  const endpoint = new URL("/api/generate", baseUrl);
  const response = await fetchImpl(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: modelId,
      prompt: "",
      stream: false,
      keep_alive: ollamaKeepAliveValue(keepAlive),
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!response.ok) {
    throw new Error(`Ollama warmup returned HTTP ${response.status}`);
  }
  await response.arrayBuffer();
}
