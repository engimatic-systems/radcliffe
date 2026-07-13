import { createServer, type ServerResponse } from "node:http";

import { builtinModels } from "@earendil-works/pi-ai/providers/all";

import { PiAgentOrchestrator } from "./orchestrator.js";
import type {
  SidecarEvent,
  ToolExecutionRequest,
  ToolExecutionResult,
  TurnRequest,
} from "./protocol.js";

const port = Number(process.env.RADCLIFFE_SIDECAR_PORT ?? "8787");
const provider = process.env.RADCLIFFE_MODEL_PROVIDER ?? "openai";
const modelId = process.env.RADCLIFFE_MODEL_ID ?? "gpt-5.1";
const gatewayUrl = process.env.RADCLIFFE_TOOL_GATEWAY_URL;
const models = builtinModels();
const model = models.getModel(provider, modelId);
if (!model) throw new Error(`unknown Pi model ${provider}/${modelId}`);

async function executeTool(
  request: ToolExecutionRequest,
  signal?: AbortSignal,
): Promise<ToolExecutionResult> {
  if (!gatewayUrl) throw new Error("RADCLIFFE_TOOL_GATEWAY_URL is required for tools");
  const response = await fetch(`${gatewayUrl}/v1/tool-calls`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw new Error(`Tool Gateway returned HTTP ${response.status}`);
  return (await response.json()) as ToolExecutionResult;
}

const orchestrator = new PiAgentOrchestrator(model, models.streamSimple, executeTool);

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
