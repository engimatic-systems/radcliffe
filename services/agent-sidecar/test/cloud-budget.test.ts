import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { StreamFn } from "@earendil-works/pi-agent-core";
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
} from "@earendil-works/pi-ai";
import { describe, expect, it, vi } from "vitest";

import { CloudBudgetController, CloudBudgetLedger } from "../src/cloud-budget.js";
import type { AnthropicPayloadCounter } from "../src/context-meter.js";

const BASIS = "anthropic/claude-haiku-4-5-20251001/test-pricing";

function model(): Model<string> {
  return {
    id: "claude-haiku-4-5-20251001",
    name: "Claude Haiku 4.5",
    api: "anthropic-messages",
    provider: "anthropic",
    baseUrl: "https://api.anthropic.com",
    reasoning: true,
    input: ["text"],
    cost: { input: 1, output: 5, cacheRead: 0.1, cacheWrite: 1.25 },
    contextWindow: 200_000,
    maxTokens: 1_000,
  };
}

function assistant(input: number, output: number, cost: number): AssistantMessage {
  return {
    role: "assistant",
    content: [{ type: "text", text: "bounded response" }],
    api: "anthropic-messages",
    provider: "anthropic",
    model: "claude-haiku-4-5-20251001",
    usage: {
      input,
      output,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: input + output,
      cost: { input: cost, output: 0, cacheRead: 0, cacheWrite: 0, total: cost },
    },
    stopReason: "stop",
    timestamp: 1,
  };
}

function payloadCounter(inputTokens: number): AnthropicPayloadCounter {
  return {
    async measureContext() {
      return {
        requestDigest: "a".repeat(64),
        rawInputTokens: inputTokens,
        conservativeInputTokens: inputTokens,
      };
    },
  };
}

async function terminal(stream: Awaited<ReturnType<StreamFn>>) {
  let result;
  for await (const event of stream) result = event;
  return result;
}

describe("cloud session budget", () => {
  it("caps all provider calls in one foreground turn and records reported usage", async () => {
    const ledger = new CloudBudgetLedger(BASIS, 0.02, 1_000, 2, 5);
    const controller = new CloudBudgetController(payloadCounter(1_000), ledger);
    const maxTokens: number[] = [];
    const responses = [assistant(900, 200, 0.003), assistant(950, 100, 0.0025)];
    const base: StreamFn = vi.fn((_model, _context, options) => {
      maxTokens.push(options?.maxTokens ?? 0);
      const stream = createAssistantMessageEventStream();
      const message = responses.shift()!;
      stream.push({ type: "done", reason: "stop", message });
      return stream;
    });
    const turnStream = controller.streamForTurn(base, "session-private-id");
    const context: Context = { messages: [] };

    await terminal(await turnStream(model(), context));
    await terminal(await turnStream(model(), context));

    expect(maxTokens).toEqual([1_000, 800]);
    expect(ledger.summary()).toMatchObject({
      sessionCount: 1,
      spentUsd: 0.0055,
      heldUsd: 0,
      requests: 2,
    });
  });

  it("returns a visible terminal error before a call that cannot fit the ceiling", async () => {
    const ledger = new CloudBudgetLedger(BASIS, 0.001, 1_000, 2, 5);
    const controller = new CloudBudgetController(payloadCounter(1_000), ledger);
    const base = vi.fn<StreamFn>();

    const result = await terminal(
      await controller.streamForTurn(base, "session-1")(model(), { messages: [] }),
    );

    expect(result).toMatchObject({
      type: "error",
      error: { stopReason: "error", errorMessage: expect.stringContaining("budget exhausted") },
    });
    expect(base).not.toHaveBeenCalled();
  });

  it("persists only a pseudonymous ledger and charges abandoned reservations on restart", () => {
    const directory = mkdtempSync(join(tmpdir(), "radcliffe-cloud-budget-"));
    const path = join(directory, "ledger.json");
    const first = new CloudBudgetLedger(BASIS, 0.02, 1_000, 2, 5, path);

    first.reserve("private-session-name", 1_000);

    expect(readFileSync(path, "utf8")).not.toContain("private-session-name");
    expect(statSync(path).mode & 0o777).toBe(0o600);
    const restarted = new CloudBudgetLedger(BASIS, 0.02, 1_000, 2, 5, path);
    expect(restarted.summary()).toMatchObject({
      spentUsd: 0.007,
      heldUsd: 0,
      recoveredReservations: 1,
    });
  });
});
