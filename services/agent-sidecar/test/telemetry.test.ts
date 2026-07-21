import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { sessionPseudonym, TelemetrySink } from "../src/telemetry.js";
import { validTurnIdentity } from "../src/protocol.js";

describe("content-free telemetry", () => {
  it("writes a bounded private event without raw session or content", () => {
    const directory = mkdtempSync(join(tmpdir(), "radcliffe-telemetry-"));
    const path = join(directory, "nested", "events.jsonl");
    try {
      const event = new TelemetrySink(path).emit({
        component: "sidecar",
        event: "active_work",
        outcome: "completed",
        traceId: `trc_${"a".repeat(32)}`,
        sessionId: "private-session-name",
        generation: 3,
        operation: "operation-123456",
        phase: "provider",
        durationMs: 12.3456,
      });
      const encoded = readFileSync(path, "utf8");
      expect(JSON.parse(encoded)).toEqual(event);
      expect(encoded).not.toContain("private-session-name");
      expect(encoded).not.toContain("message");
      expect(event.session).toBe(sessionPseudonym("private-session-name"));
      expect(statSync(path).mode & 0o777).toBe(0o600);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("rejects unbounded correlation and dimensions", () => {
    const sink = new TelemetrySink();
    expect(() =>
      sink.emit({
        component: "sidecar",
        event: "provider error with content",
        outcome: "failed",
        traceId: "invalid",
        sessionId: "session",
        generation: 1,
        operation: "operation",
        phase: "provider",
      }),
    ).toThrow();
  });

  it("requires bounded server turn correlation", () => {
    const request = {
      requestId: "request",
      sessionId: "session",
      generation: 1,
      operationId: "operation-123456",
      cancellationId: "cancellation-123456",
      traceId: `trc_${"d".repeat(32)}`,
      packets: [],
    };
    expect(validTurnIdentity(request)).toBe(true);
    expect(validTurnIdentity({ ...request, traceId: "private message" })).toBe(false);
  });
});
