import { createHash, randomUUID } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

import type { StreamFn } from "@earendil-works/pi-agent-core";
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type Usage,
} from "@earendil-works/pi-ai";

import type { AnthropicPayloadCounter } from "./context-meter.js";

const LEDGER_SCHEMA = "radcliffe-cloud-budget-v1";

interface LedgerRow {
  basis: string;
  spentUsd: number;
  heldUsd: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  requests: number;
  recoveredReservations: number;
}

interface PersistedLedger {
  schema: typeof LEDGER_SCHEMA;
  sessions: Record<string, LedgerRow>;
}

export interface CloudBudgetReservation {
  reservationId: string;
  sessionKey: string;
  inputCostUsd: number;
  reservedUsd: number;
  outputTokenLimit: number;
}

export interface CloudBudgetSummary {
  basis: string;
  limitUsd: number;
  maxOutputTokensPerTurn: number;
  sessionCount: number;
  spentUsd: number;
  heldUsd: number;
  requests: number;
  recoveredReservations: number;
}

export class CloudBudgetExhaustedError extends Error {}

function finiteNonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function emptyRow(basis: string): LedgerRow {
  return {
    basis,
    spentUsd: 0,
    heldUsd: 0,
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    requests: 0,
    recoveredReservations: 0,
  };
}

function validRow(value: unknown): value is LedgerRow {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<LedgerRow>;
  return (
    typeof row.basis === "string" &&
    finiteNonNegative(row.spentUsd) &&
    finiteNonNegative(row.heldUsd) &&
    Number.isSafeInteger(row.inputTokens) &&
    row.inputTokens! >= 0 &&
    Number.isSafeInteger(row.outputTokens) &&
    row.outputTokens! >= 0 &&
    Number.isSafeInteger(row.cacheReadTokens) &&
    row.cacheReadTokens! >= 0 &&
    Number.isSafeInteger(row.cacheWriteTokens) &&
    row.cacheWriteTokens! >= 0 &&
    Number.isSafeInteger(row.requests) &&
    row.requests! >= 0 &&
    Number.isSafeInteger(row.recoveredReservations) &&
    row.recoveredReservations! >= 0
  );
}

export class CloudBudgetLedger {
  private readonly rows = new Map<string, LedgerRow>();
  private readonly activeReservations = new Set<string>();

  constructor(
    readonly basis: string,
    readonly limitUsd: number,
    readonly maxOutputTokensPerTurn: number,
    private readonly inputRatePerMillion: number,
    private readonly outputRatePerMillion: number,
    private readonly path?: string,
  ) {
    if (
      !basis ||
      !Number.isFinite(limitUsd) ||
      limitUsd <= 0 ||
      !Number.isSafeInteger(maxOutputTokensPerTurn) ||
      maxOutputTokensPerTurn <= 0 ||
      !Number.isFinite(inputRatePerMillion) ||
      inputRatePerMillion <= 0 ||
      !Number.isFinite(outputRatePerMillion) ||
      outputRatePerMillion <= 0
    ) {
      throw new Error("cloud budget configuration is invalid");
    }
    this.load();
  }

  reserve(
    sessionId: string,
    conservativeInputTokens: number,
    requestedOutputTokens = this.maxOutputTokensPerTurn,
  ): CloudBudgetReservation {
    if (!sessionId || !Number.isSafeInteger(conservativeInputTokens) || conservativeInputTokens < 0) {
      throw new Error("cloud budget reservation is invalid");
    }
    const sessionKey = this.sessionKey(sessionId);
    const row = this.rows.get(sessionKey) ?? emptyRow(this.basis);
    const availableUsd = Math.max(0, this.limitUsd - row.spentUsd - row.heldUsd);
    const inputCostUsd = (conservativeInputTokens * this.inputRatePerMillion) / 1_000_000;
    const boundedRequestedOutput = Math.min(
      this.maxOutputTokensPerTurn,
      Math.max(0, Math.floor(requestedOutputTokens)),
    );
    const affordableOutputTokens = Math.floor(
      (Math.max(0, availableUsd - inputCostUsd) * 1_000_000) / this.outputRatePerMillion,
    );
    const outputTokenLimit = Math.min(boundedRequestedOutput, affordableOutputTokens);
    if (outputTokenLimit < 1) {
      throw new CloudBudgetExhaustedError(
        `cloud session budget exhausted for ${this.basis}: ` +
          `$${row.spentUsd.toFixed(4)} spent and $${row.heldUsd.toFixed(4)} held ` +
          `of $${this.limitUsd.toFixed(4)}`,
      );
    }
    const reservedUsd =
      inputCostUsd + (outputTokenLimit * this.outputRatePerMillion) / 1_000_000;
    const reservationId = randomUUID();
    row.heldUsd += reservedUsd;
    this.rows.set(sessionKey, row);
    this.activeReservations.add(reservationId);
    this.persist();
    return {
      reservationId,
      sessionKey,
      inputCostUsd,
      reservedUsd,
      outputTokenLimit,
    };
  }

  settle(reservation: CloudBudgetReservation, usage: Usage): void {
    if (!this.activeReservations.delete(reservation.reservationId)) return;
    const row = this.rows.get(reservation.sessionKey);
    if (!row) throw new Error("cloud budget reservation lost its session record");
    row.heldUsd = Math.max(0, row.heldUsd - reservation.reservedUsd);
    const reportedCost = finiteNonNegative(usage.cost.total) ? usage.cost.total : 0;
    row.spentUsd += Math.max(reportedCost, reservation.inputCostUsd);
    row.inputTokens += Math.max(0, usage.input);
    row.outputTokens += Math.max(0, usage.output);
    row.cacheReadTokens += Math.max(0, usage.cacheRead);
    row.cacheWriteTokens += Math.max(0, usage.cacheWrite);
    row.requests += 1;
    this.persist();
  }

  settleConservatively(reservation: CloudBudgetReservation): void {
    if (!this.activeReservations.delete(reservation.reservationId)) return;
    const row = this.rows.get(reservation.sessionKey);
    if (!row) throw new Error("cloud budget reservation lost its session record");
    row.heldUsd = Math.max(0, row.heldUsd - reservation.reservedUsd);
    row.spentUsd += reservation.reservedUsd;
    row.requests += 1;
    this.persist();
  }

  summary(): CloudBudgetSummary {
    const rows = [...this.rows.values()].filter((row) => row.basis === this.basis);
    return {
      basis: this.basis,
      limitUsd: this.limitUsd,
      maxOutputTokensPerTurn: this.maxOutputTokensPerTurn,
      sessionCount: rows.length,
      spentUsd: rows.reduce((total, row) => total + row.spentUsd, 0),
      heldUsd: rows.reduce((total, row) => total + row.heldUsd, 0),
      requests: rows.reduce((total, row) => total + row.requests, 0),
      recoveredReservations: rows.reduce(
        (total, row) => total + row.recoveredReservations,
        0,
      ),
    };
  }

  private sessionKey(sessionId: string): string {
    return createHash("sha256")
      .update(`${LEDGER_SCHEMA}\0${this.basis}\0${sessionId}`)
      .digest("hex");
  }

  private load(): void {
    if (!this.path || !existsSync(this.path)) return;
    const parsed = JSON.parse(readFileSync(this.path, "utf8")) as Partial<PersistedLedger>;
    if (parsed.schema !== LEDGER_SCHEMA || typeof parsed.sessions !== "object") {
      throw new Error("cloud budget ledger schema is invalid");
    }
    let recovered = false;
    for (const [key, candidate] of Object.entries(parsed.sessions ?? {})) {
      if (!/^[a-f0-9]{64}$/.test(key) || !validRow(candidate)) {
        throw new Error("cloud budget ledger contains an invalid session record");
      }
      const row = { ...candidate };
      if (row.heldUsd > 0) {
        row.spentUsd += row.heldUsd;
        row.heldUsd = 0;
        row.recoveredReservations += 1;
        recovered = true;
      }
      this.rows.set(key, row);
    }
    if (recovered) this.persist();
  }

  private persist(): void {
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.tmp-${process.pid}`;
    const document: PersistedLedger = {
      schema: LEDGER_SCHEMA,
      sessions: Object.fromEntries(this.rows),
    };
    writeFileSync(temporary, `${JSON.stringify(document)}\n`, { encoding: "utf8", mode: 0o600 });
    chmodSync(temporary, 0o600);
    renameSync(temporary, this.path);
    chmodSync(this.path, 0o600);
  }
}

function budgetErrorMessage(model: Model<string>, message: string): AssistantMessage {
  return {
    role: "assistant",
    content: [],
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason: "error",
    errorMessage: message,
    timestamp: Date.now(),
  };
}

function budgetErrorStream(model: Model<string>, message: string) {
  const stream = createAssistantMessageEventStream();
  const error = budgetErrorMessage(model, message);
  stream.push({ type: "error", reason: "error", error });
  return stream;
}

export class CloudBudgetController {
  constructor(
    private readonly payloadMeter: AnthropicPayloadCounter,
    readonly ledger: CloudBudgetLedger,
  ) {}

  streamForTurn(baseStream: StreamFn, sessionId: string): StreamFn {
    let remainingOutputTokens = this.ledger.maxOutputTokensPerTurn;
    return async (model, context: Context, options) => {
      let reservation: CloudBudgetReservation;
      try {
        const measurement = await this.payloadMeter.measureContext(context);
        reservation = this.ledger.reserve(
          sessionId,
          measurement.conservativeInputTokens,
          remainingOutputTokens,
        );
      } catch (error) {
        return budgetErrorStream(
          model,
          error instanceof Error ? error.message : "cloud budget preflight failed",
        );
      }

      let source;
      try {
        source = await baseStream(model, context, {
          ...options,
          maxTokens: reservation.outputTokenLimit,
        });
      } catch (error) {
        this.ledger.settleConservatively(reservation);
        return budgetErrorStream(
          model,
          error instanceof Error ? error.message : "cloud provider failed",
        );
      }

      const forwarded = createAssistantMessageEventStream();
      void (async () => {
        let settled = false;
        try {
          for await (const event of source) {
            if (event.type === "done" || event.type === "error") {
              const message = event.type === "done" ? event.message : event.error;
              this.ledger.settle(reservation, message.usage);
              remainingOutputTokens = Math.max(0, remainingOutputTokens - message.usage.output);
              settled = true;
            }
            forwarded.push(event);
          }
          if (!settled) {
            this.ledger.settleConservatively(reservation);
            forwarded.push({
              type: "error",
              reason: "error",
              error: budgetErrorMessage(model, "cloud provider stream ended without usage"),
            });
          }
        } catch (error) {
          if (!settled) this.ledger.settleConservatively(reservation);
          const failed = budgetErrorStream(
            model,
            error instanceof Error ? error.message : "cloud provider stream failed",
          );
          for await (const event of failed) forwarded.push(event);
        }
      })();
      return forwarded;
    };
  }
}
