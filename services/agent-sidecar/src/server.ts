import { createServer, type ServerResponse } from "node:http";

import type { StreamFn } from "@earendil-works/pi-agent-core";
import { streamSimple as streamOpenAICompletions } from "@earendil-works/pi-ai/api/openai-completions";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import type { Model } from "@earendil-works/pi-ai";

import { PiAgentOrchestrator } from "./orchestrator.js";
import { assertProviderAllowed } from "./provider-policy.js";
import type {
  SidecarEvent,
  ToolExecutionRequest,
  ToolExecutionResult,
  TurnRequest,
} from "./protocol.js";

const port = Number(process.env.RADCLIFFE_SIDECAR_PORT ?? "8787");
const provider = process.env.RADCLIFFE_MODEL_PROVIDER ?? "ollama";
const modelId = process.env.RADCLIFFE_MODEL_ID ?? "qwen3:8b";
assertProviderAllowed(provider, process.env.RADCLIFFE_ALLOW_CLOUD_MODELS);
const gatewayUrl = process.env.RADCLIFFE_TOOL_GATEWAY_URL;
const internalToken = process.env.RADCLIFFE_INTERNAL_TOKEN;
const models = builtinModels();
const ollamaModel: Model<"openai-completions"> = {
  id: modelId,
  name: modelId,
  api: "openai-completions",
  provider: "ollama",
  baseUrl: process.env.RADCLIFFE_OLLAMA_URL ?? "http://127.0.0.1:11434/v1",
  reasoning: false,
  input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  contextWindow: 65536,
  maxTokens: 8192,
};
const model = provider === "ollama" ? ollamaModel : models.getModel(provider, modelId);
if (!model) throw new Error(`unknown Pi model ${provider}/${modelId}`);
const streamFn: StreamFn =
  provider === "ollama"
    ? (selectedModel, context, options) =>
        streamOpenAICompletions(
          selectedModel as Model<"openai-completions">,
          context,
          { ...options, apiKey: "ollama" },
        )
    : models.streamSimple;

async function executeTool(
  request: ToolExecutionRequest,
  signal?: AbortSignal,
): Promise<ToolExecutionResult> {
  if (!gatewayUrl || !internalToken) {
    throw new Error("RADCLIFFE_TOOL_GATEWAY_URL and RADCLIFFE_INTERNAL_TOKEN are required");
  }
  const response = await fetch(`${gatewayUrl}/v1/tool-calls`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${internalToken}`,
    },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw new Error(`Tool Gateway returned HTTP ${response.status}`);
  return (await response.json()) as ToolExecutionResult;
}

const orchestrator = new PiAgentOrchestrator(model, streamFn, executeTool);

function writeEvent(response: ServerResponse, event: SidecarEvent): void {
  response.write(`${JSON.stringify(event)}\n`);
}

createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/healthz") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (request.method !== "POST" || request.url !== "/v1/turns") {
    response.writeHead(404).end();
    return;
  }
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  let turn: TurnRequest;
  try {
    turn = JSON.parse(Buffer.concat(chunks).toString("utf8")) as TurnRequest;
  } catch {
    response.writeHead(400, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "invalid JSON" }));
    return;
  }
  response.writeHead(200, {
    "content-type": "application/x-ndjson",
    "cache-control": "no-store",
  });
  const controller = new AbortController();
  response.on("close", () => {
    if (!response.writableEnded) controller.abort();
  });
  try {
    await orchestrator.run(turn, (event) => writeEvent(response, event), controller.signal);
  } finally {
    response.end();
  }
}).listen(port, "127.0.0.1");
