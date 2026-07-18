import {
  createFauxCore,
  fauxAssistantMessage,
  fauxToolCall,
} from "@earendil-works/pi-ai/providers/faux";
import { describe, expect, it, vi } from "vitest";

import { convertRadcliffeContext, PiAgentOrchestrator } from "../src/orchestrator.js";
import type { ContextMeter } from "../src/context-meter.js";
import type { ContextPacket, ContextUsage, SidecarEvent, TurnRequest } from "../src/protocol.js";

function packet(
  messageId: string,
  content: string,
  options: Partial<ContextPacket> = {},
): ContextPacket {
  return {
    projectionVersion: "context-v2",
    messageId,
    role: "user",
    content,
    source: "user",
    annotations: [["projection", "context-v2"]],
    include: true,
    timestamp: 1,
    ...options,
  };
}

function request(packets: ContextPacket[]): TurnRequest {
  return { requestId: "request-1", sessionId: "session-1", generation: 1, packets };
}

describe("PiAgentOrchestrator", () => {
  it("streams a normal answer and reconstructs projected history", async () => {
    const faux = createFauxCore({ provider: "radcliffe-test" });
    faux.setResponses([fauxAssistantMessage("A concise answer.")]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn();
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet("old-model", "Earlier answer", { role: "assistant", source: "model" }),
        packet("new-user", "What is Radcliffe?"),
      ]),
      (event) => {
        events.push(event);
      },
    );

    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "A concise answer.",
    });
    expect(events.at(-1)).toEqual({ type: "complete", requestId: "request-1" });
    expect(tool).not.toHaveBeenCalled();
  });

  it("executes read_source sequentially and emits its lifecycle", async () => {
    const faux = createFauxCore({ provider: "radcliffe-tool-test" });
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("read_source", { path: "demo.md" }, { id: "call-1" }), {
        stopReason: "toolUse",
      }),
      fauxAssistantMessage("I handed the canonical source to Radcliffe."),
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn(async (_request, _signal, onUpdate) => {
      onUpdate?.({ status: "reading" });
      return { modelContent: "source:demo.md", details: { sourceMessageId: "source-1" } };
    });
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([packet("new-user", "Read demo.md")]),
      (event) => {
        events.push(event);
      },
    );

    expect(tool).toHaveBeenCalledOnce();
    expect(tool.mock.calls[0]?.[0]).toMatchObject({
      sessionId: "session-1",
      generation: 1,
      callId: "call-1",
      toolName: "read_source",
      arguments: { path: "demo.md" },
    });
    expect(events.map((event) => event.type)).toEqual(
      expect.arrayContaining(["tool_start", "tool_update", "tool_end", "model_complete"]),
    );
  });

  it("filters excluded packets and labels canonical sources", () => {
    const converted = convertRadcliffeContext([
      { role: "radcliffe", packet: packet("secret", "key", { include: false }) },
      {
        role: "radcliffe",
        packet: packet("source", "Canonical", {
          role: "source",
          source: "text_source",
          annotations: [["locator", "demo.md"]],
        }),
      },
    ]);

    expect(converted).toHaveLength(1);
    expect(converted[0]?.role).toBe("user");
    expect(converted[0]?.content).toContain("[Radcliffe text_source from demo.md]");
  });

  it("rejects overflow before invoking the model provider", async () => {
    const faux = createFauxCore({ provider: "radcliffe-overflow-test" });
    faux.setResponses([fauxAssistantMessage("must not run")]);
    const usage: ContextUsage = {
      modelId: "local-test",
      contextLimit: 100,
      projectedInputTokens: 90,
      reservedOutputTokens: 20,
      remainingTokens: 0,
      percentage: 100,
      projectionVersion: "context-v2",
      measurementBasis: "test-exact",
      exact: true,
      overflow: true,
      requestDigest: "a".repeat(64),
    };
    const meter: ContextMeter = { measure: vi.fn(async () => usage) };
    const orchestrator = new PiAgentOrchestrator(
      faux.getModel(),
      faux.streamSimple,
      vi.fn(),
      meter,
    );

    await expect(
      orchestrator.run(request([packet("current", "Too much context")]), vi.fn()),
    ).rejects.toThrow("context overflow");
    expect(faux.state.callCount).toBe(0);
  });
});
