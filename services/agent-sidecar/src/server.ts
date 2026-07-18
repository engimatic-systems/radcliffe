import { createServer, type ServerResponse } from "node:http";

import type { StreamFn } from "@earendil-works/pi-agent-core";
import { streamSimple as streamAnthropicMessages } from "@earendil-works/pi-ai/api/anthropic-messages";
import { streamSimple as streamOpenAICompletions } from "@earendil-works/pi-ai/api/openai-completions";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { hasApi, type Model } from "@earendil-works/pi-ai";

import { CloudBudgetController, CloudBudgetLedger } from "./cloud-budget.js";
import {
  AnthropicContextMeter,
  AnthropicPayloadMeter,
  OllamaContextMeter,
} from "./context-meter.js";
import { PiAgentOrchestrator } from "./orchestrator.js";
import { createOllamaModel } from "./ollama-model.js";
import { warmOllama } from "./ollama-runtime.js";
import {
  ANTHROPIC_DEBUG_PRICING_BASIS,
  assertAnthropicDebugModelContract,
  assertProviderAllowed,
  assertSupportedModelProfile,
  takeEnvironmentSecret,
} from "./provider-policy.js";
import { TelemetrySink } from "./telemetry.js";
import type {
  SidecarEvent,
  ToolExecutionRequest,
  ToolExecutionResult,
  TurnRequest,
} from "./protocol.js";
import { validTurnIdentity } from "./protocol.js";

const port = Number(process.env.RADCLIFFE_SIDECAR_PORT ?? "8787");
const provider = process.env.RADCLIFFE_MODEL_PROVIDER ?? "ollama";
const modelId = process.env.RADCLIFFE_MODEL_ID ?? "qwen3:8b";
assertProviderAllowed(provider, process.env.RADCLIFFE_ALLOW_CLOUD_MODELS);
assertSupportedModelProfile(provider, modelId);
const gatewayUrl = process.env.RADCLIFFE_TOOL_GATEWAY_URL;
const internalToken = process.env.RADCLIFFE_INTERNAL_TOKEN;
const telemetry = new TelemetrySink(
  process.env.RADCLIFFE_TELEMETRY_PATH ?? "var/telemetry/events.jsonl",
);
const models = builtinModels();
const ollamaModel = createOllamaModel({
  modelId,
  baseUrl: process.env.RADCLIFFE_OLLAMA_URL ?? "http://127.0.0.1:11434/v1",
  contextWindow: Number(process.env.RADCLIFFE_MODEL_CONTEXT_LIMIT ?? "40960"),
  maxTokens: Number(process.env.RADCLIFFE_MODEL_RESERVED_OUTPUT_TOKENS ?? "8192"),
});
const selectedModel = provider === "ollama" ? ollamaModel : models.getModel(provider, modelId);
if (!selectedModel) throw new Error(`unknown Pi model ${provider}/${modelId}`);
if (provider === "anthropic") assertAnthropicDebugModelContract(selectedModel);
const cloudMaxOutputTokens =
  provider === "anthropic"
    ? positiveInteger(
        "RADCLIFFE_CLOUD_MAX_OUTPUT_TOKENS",
        process.env.RADCLIFFE_CLOUD_MAX_OUTPUT_TOKENS ?? "1024",
      )
    : selectedModel.maxTokens;
const model: Model<string> =
  provider === "anthropic"
    ? { ...selectedModel, maxTokens: Math.min(selectedModel.maxTokens, cloudMaxOutputTokens) }
    : selectedModel;
const anthropicApiKey =
  provider === "anthropic"
    ? takeEnvironmentSecret(process.env, "ANTHROPIC_API_KEY")
    : undefined;
if (provider === "anthropic" && !anthropicApiKey) {
  throw new Error("ANTHROPIC_API_KEY is required for the cloud profile");
}
if (provider === "ollama") {
  await warmOllama({
    baseUrl: ollamaModel.baseUrl,
    modelId,
    keepAlive: process.env.RADCLIFFE_OLLAMA_KEEP_ALIVE ?? "-1",
  });
}
const streamFn: StreamFn =
  provider === "ollama"
    ? (selectedModel, context, options) =>
        streamOpenAICompletions(
          selectedModel as Model<"openai-completions">,
          context,
          { ...options, apiKey: "ollama" },
        )
    : (selectedModel, context, options) =>
        streamAnthropicMessages(
          selectedModel as Model<"anthropic-messages">,
          context,
          { ...options, apiKey: anthropicApiKey },
        );

async function executeTool(
  request: ToolExecutionRequest,
  signal?: AbortSignal,
): Promise<ToolExecutionResult> {
  telemetry.emit({
    component: "sidecar",
    event: "tool",
    outcome: "started",
    traceId: request.traceId,
    sessionId: request.sessionId,
    generation: request.generation,
    operation: request.operationId,
    phase: "execution",
  });
  if (!gatewayUrl || !internalToken) {
    telemetry.emit({
      component: "sidecar",
      event: "tool_error",
      outcome: "failed",
      traceId: request.traceId,
      sessionId: request.sessionId,
      generation: request.generation,
      operation: request.operationId,
      phase: "configuration",
    });
    throw new Error("RADCLIFFE_TOOL_GATEWAY_URL and RADCLIFFE_INTERNAL_TOKEN are required");
  }
  let response: Response;
  try {
    response = await fetch(`${gatewayUrl}/v1/tool-calls`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${internalToken}`,
      },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    telemetry.emit({
      component: "sidecar",
      event: "tool_error",
      outcome: "failed",
      traceId: request.traceId,
      sessionId: request.sessionId,
      generation: request.generation,
      operation: request.operationId,
      phase: "gateway",
    });
    throw error;
  }
  if (!response.ok) {
    telemetry.emit({
      component: "sidecar",
      event: "tool_error",
      outcome: "failed",
      traceId: request.traceId,
      sessionId: request.sessionId,
      generation: request.generation,
      operation: request.operationId,
      phase: "gateway",
    });
    throw new Error(`Tool Gateway returned HTTP ${response.status}`);
  }
  telemetry.emit({
    component: "sidecar",
    event: "tool",
    outcome: "completed",
    traceId: request.traceId,
    sessionId: request.sessionId,
    generation: request.generation,
    operation: request.operationId,
    phase: "execution",
  });
  return (await response.json()) as ToolExecutionResult;
}

let cloudBudget: CloudBudgetController | undefined;
let contextMeter;
if (provider === "ollama") {
  contextMeter = new OllamaContextMeter({ model: ollamaModel });
} else {
  if (!hasApi(model, "anthropic-messages")) {
    throw new Error("the bounded Anthropic profile requires the Messages API");
  }
  const payloadMeter = new AnthropicPayloadMeter({ model, apiKey: anthropicApiKey! });
  contextMeter = new AnthropicContextMeter(model, payloadMeter);
  const sessionBudgetUsd = positiveNumber(
    "RADCLIFFE_CLOUD_SESSION_BUDGET_USD",
    process.env.RADCLIFFE_CLOUD_SESSION_BUDGET_USD ?? "0.25",
  );
  const conservativeInputRate = Math.max(
    model.cost.input,
    model.cost.cacheWrite,
    model.cost.input * 2,
  );
  cloudBudget = new CloudBudgetController(
    payloadMeter,
    new CloudBudgetLedger(
      ANTHROPIC_DEBUG_PRICING_BASIS,
      sessionBudgetUsd,
      model.maxTokens,
      conservativeInputRate,
      model.cost.output,
      process.env.RADCLIFFE_CLOUD_BUDGET_LEDGER_PATH ?? "var/cloud-budget.json",
    ),
  );
}
const orchestrator = new PiAgentOrchestrator(
  model,
  streamFn,
  executeTool,
  contextMeter,
  cloudBudget ? (request) => cloudBudget.streamForTurn(streamFn, request.sessionId) : undefined,
);
let activeTurns = 0;
let cancelledTurns = 0;

function writeEvent(response: ServerResponse, event: SidecarEvent): void {
  response.write(`${JSON.stringify(event)}\n`);
}

function positiveInteger(name: string, value: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function positiveNumber(name: string, value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return parsed;
}

createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/healthz") {
    const budget = cloudBudget?.ledger.summary();
    response.writeHead(200, { "content-type": "application/json" });
    response.end(
      JSON.stringify({
        status: "ok",
        active_turns: activeTurns,
        cancelled_turns: cancelledTurns,
        provider,
        model_id: model.id,
        data_boundary: provider === "ollama" ? "local" : "cloud_anthropic",
        context_accounting:
          provider === "ollama"
            ? "ollama_openai_prompt_tokens_exact"
            : "anthropic_count_tokens_conservative_estimate",
        ...(budget
          ? {
              cloud_budget: {
                basis: budget.basis,
                limit_usd: budget.limitUsd,
                max_output_tokens_per_turn: budget.maxOutputTokensPerTurn,
                session_count: budget.sessionCount,
                spent_usd: budget.spentUsd,
                held_usd: budget.heldUsd,
                requests: budget.requests,
                recovered_reservations: budget.recoveredReservations,
              },
            }
          : {}),
      }),
    );
    return;
  }
  const isTurn = request.method === "POST" && request.url === "/v1/turns";
  const isContextUsage = request.method === "POST" && request.url === "/v1/context-usage";
  const isAgentConfirmation =
    request.method === "POST" && request.url === "/v1/agent-control-confirmations";
  if (!isTurn && !isContextUsage && !isAgentConfirmation) {
    response.writeHead(404).end();
    return;
  }
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  if (isAgentConfirmation) {
    if (!gatewayUrl || !internalToken) {
      response.writeHead(503, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "agent-control gateway is unavailable" }));
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      response.writeHead(400, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "invalid JSON" }));
      return;
    }
    const gatewayResponse = await fetch(`${gatewayUrl}/v1/internal/agent-control/confirmations`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${internalToken}`,
      },
      body: JSON.stringify(payload),
    });
    response.writeHead(gatewayResponse.status, { "content-type": "application/json" });
    response.end(await gatewayResponse.text());
    return;
  }
  let turn: TurnRequest;
  try {
    turn = JSON.parse(Buffer.concat(chunks).toString("utf8")) as TurnRequest;
  } catch {
    response.writeHead(400, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "invalid JSON" }));
    return;
  }
  if (isContextUsage) {
    if (!contextMeter) {
      response.writeHead(501, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "exact context accounting is unavailable" }));
      return;
    }
    try {
      const usage = await orchestrator.measure(turn);
      response.writeHead(200, {
        "content-type": "application/json",
        "cache-control": "no-store",
      });
      response.end(JSON.stringify(usage));
    } catch (error) {
      response.writeHead(503, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          error: error instanceof Error ? error.message : "context accounting failed",
        }),
      );
    }
    return;
  }
  if (!validTurnIdentity(turn)) {
    response.writeHead(400, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "invalid turn correlation" }));
    return;
  }
  if (contextMeter) {
    try {
      const usage = await orchestrator.measure(turn);
      if (usage.overflow) {
        response.writeHead(422, { "content-type": "application/json" });
        response.end(
          JSON.stringify({
            error:
              `context overflow: ${usage.projectedInputTokens} input + ` +
              `${usage.reservedOutputTokens} reserved > ${usage.contextLimit} token limit`,
            usage,
          }),
        );
        return;
      }
    } catch (error) {
      response.writeHead(503, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          error: error instanceof Error ? error.message : "context accounting failed",
        }),
      );
      return;
    }
  }
  response.writeHead(200, {
    "content-type": "application/x-ndjson",
    "cache-control": "no-store",
  });
  activeTurns += 1;
  const turnStarted = performance.now();
  let turnOutcome = "completed";
  telemetry.emit({
    component: "sidecar",
    event: "active_work",
    outcome: "started",
    traceId: turn.traceId!,
    sessionId: turn.sessionId,
    generation: turn.generation,
    operation: turn.operationId!,
    phase: "provider",
  });
  const controller = new AbortController();
  response.on("close", () => {
    if (!response.writableEnded && !controller.signal.aborted) {
      cancelledTurns += 1;
      turnOutcome = "interrupted";
      controller.abort();
    }
  });
  try {
    await orchestrator.run(turn, (event) => writeEvent(response, event), controller.signal);
  } catch (error) {
    if (controller.signal.aborted) {
      turnOutcome = "interrupted";
    } else {
      turnOutcome = "failed";
      telemetry.emit({
        component: "sidecar",
        event: "provider_error",
        outcome: "failed",
        traceId: turn.traceId!,
        sessionId: turn.sessionId,
        generation: turn.generation,
        operation: turn.operationId!,
        phase: "provider",
        durationMs: performance.now() - turnStarted,
      });
      writeEvent(response, {
        type: "error",
        requestId: turn.requestId,
        message: error instanceof Error ? error.message : "provider failed",
      });
    }
  } finally {
    activeTurns -= 1;
    telemetry.emit({
      component: "sidecar",
      event: "active_work",
      outcome: turnOutcome,
      traceId: turn.traceId!,
      sessionId: turn.sessionId,
      generation: turn.generation,
      operation: turn.operationId!,
      phase: "provider",
      durationMs: performance.now() - turnStarted,
    });
    response.end();
  }
}).listen(port, "127.0.0.1");
