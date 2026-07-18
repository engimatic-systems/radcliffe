import { createHash } from "node:crypto";
import { appendFileSync, chmodSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export const TELEMETRY_SCHEMA = "radcliffe-content-free-telemetry-v1";
const TRACE_ID = /^trc_[0-9a-f]{32}$/;
const NAME = /^[a-z][a-z0-9_]{0,47}$/;
const OUTCOMES = new Set([
  "started",
  "succeeded",
  "completed",
  "interrupted",
  "cancelled",
  "failed",
  "reconnected",
  "ready",
  "warning",
]);

export interface TelemetryInput {
  component: "sidecar" | "control";
  event: string;
  outcome: string;
  traceId: string;
  sessionId: string;
  generation: number;
  operation: string;
  phase: string;
  durationMs?: number;
}

export function sessionPseudonym(sessionId: string): string {
  const digest = createHash("sha256")
    .update(`radcliffe-session-v1\0${sessionId}`)
    .digest("hex");
  return `ses_${digest.slice(0, 20)}`;
}

export class TelemetrySink {
  constructor(private readonly path?: string) {}

  emit(input: TelemetryInput): Record<string, unknown> {
    if (!TRACE_ID.test(input.traceId)) throw new Error("telemetry trace ID is invalid");
    if (!NAME.test(input.event) || !NAME.test(input.phase)) {
      throw new Error("telemetry event or phase is invalid");
    }
    if (!OUTCOMES.has(input.outcome)) throw new Error("telemetry outcome is invalid");
    if (!Number.isSafeInteger(input.generation) || input.generation < 0) {
      throw new Error("telemetry generation is invalid");
    }
    if (input.operation.length > 128) throw new Error("telemetry operation is too long");
    if (
      input.durationMs !== undefined &&
      (!Number.isFinite(input.durationMs) || input.durationMs < 0 || input.durationMs > 86_400_000)
    ) {
      throw new Error("telemetry duration is invalid");
    }
    const event = {
      schema: TELEMETRY_SCHEMA,
      occurred_at: new Date().toISOString(),
      component: input.component,
      event: input.event,
      outcome: input.outcome,
      trace_id: input.traceId,
      session: sessionPseudonym(input.sessionId),
      generation: input.generation,
      operation: input.operation,
      phase: input.phase,
      duration_ms: input.durationMs === undefined ? null : Math.round(input.durationMs * 1000) / 1000,
    };
    if (this.path) {
      try {
        mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
        chmodSync(dirname(this.path), 0o700);
        appendFileSync(this.path, `${JSON.stringify(event)}\n`, { encoding: "utf8", mode: 0o600 });
        chmodSync(this.path, 0o600);
      } catch {
        // Telemetry must never take down an active local voice turn.
      }
    }
    return event;
  }
}
