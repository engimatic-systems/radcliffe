import {
  createFauxCore,
  fauxAssistantMessage,
  fauxToolCall,
} from "@earendil-works/pi-ai/providers/faux";
import { describe, expect, it, vi } from "vitest";

import {
  convertRadcliffeContext,
  PiAgentOrchestrator,
  RADCLIFFE_SYSTEM_PROMPT,
} from "../src/orchestrator.js";
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
  return {
    requestId: "request-1",
    sessionId: "session-1",
    generation: 1,
    operationId: "operation-123456",
    traceId: `trc_${"c".repeat(32)}`,
    packets,
  };
}

describe("PiAgentOrchestrator", () => {
  it("instructs the model to normalize spoken filename punctuation", () => {
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("dot M D");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("dash");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("list_sources first");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("No Emojis");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("Do not use emoji characters");
  });

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
    const sourceEnvelope = "opaque-canonical-envelope-base64";
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("read_source", { path: "demo.md", rate: "fast" }, { id: "call-1" }),
        {
          stopReason: "toolUse",
        },
      ),
      (context) => {
        const toolResult = context.messages.find((message) => message.role === "toolResult");
        expect(toolResult?.content).toEqual([{ type: "text", text: "source:demo.md" }]);
        expect(JSON.stringify(toolResult?.content)).not.toContain(sourceEnvelope);
        return fauxAssistantMessage("I handed the canonical source to Radcliffe.");
      },
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn(async (_request, _signal, onUpdate) => {
      onUpdate?.({ status: "reading" });
      return {
        modelContent: "source:demo.md",
        details: {
          sourceMessageId: "source-1",
          sourceEnvelopeProtoBase64: sourceEnvelope,
        },
      };
    });
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([packet("new-user", "Read demo.md quickly")]),
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
      arguments: { path: "demo.md", rate: "fast" },
    });
    expect(events.map((event) => event.type)).toEqual(
      expect.arrayContaining(["tool_start", "tool_update", "tool_end", "model_complete"]),
    );
    expect(events.find((event) => event.type === "tool_end")).toMatchObject({
      result: { details: { sourceEnvelopeProtoBase64: sourceEnvelope } },
    });
  });

  it("discovers an informally named source before reading its canonical bytes", async () => {
    const faux = createFauxCore({ provider: "radcliffe-catalog-test" });
    const sourceEnvelope = "opaque-canonical-envelope-base64";
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall("list_sources", {}, { id: "list-1" }), {
        stopReason: "toolUse",
      }),
      (context) => {
        const catalog = context.messages.find(
          (message) =>
            message.role === "toolResult" &&
            message.toolCallId === "list-1",
        );
        expect(catalog?.content).toEqual([
          {
            type: "text",
            text: '{"paths":["radcliffe-demo.md"],"truncated":false}',
          },
        ]);
        return fauxAssistantMessage(
          fauxToolCall("read_source", { path: "radcliffe-demo.md" }, { id: "read-1" }),
          { stopReason: "toolUse" },
        );
      },
      (context) => {
        const read = context.messages.find(
          (message) =>
            message.role === "toolResult" &&
            message.toolCallId === "read-1",
        );
        expect(read?.content).toEqual([{ type: "text", text: "source:radcliffe-demo.md" }]);
        expect(JSON.stringify(read?.content)).not.toContain(sourceEnvelope);
        return fauxAssistantMessage("The authorized source is being read.");
      },
    ]);
    const tool = vi.fn(async (toolRequest) => {
      if (toolRequest.toolName === "list_sources") {
        return {
          modelContent: '{"paths":["radcliffe-demo.md"],"truncated":false}',
          details: { catalogCount: "1", truncated: "false" },
        };
      }
      return {
        modelContent: "source:radcliffe-demo.md",
        details: {
          sourceMessageId: "source-1",
          sourceEnvelopeProtoBase64: sourceEnvelope,
        },
      };
    });
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([packet("new-user", "Read the demo file")]),
      (event) => {
        events.push(event);
      },
    );

    expect(tool).toHaveBeenCalledTimes(2);
    expect(tool.mock.calls.map((call) => call[0])).toEqual([
      expect.objectContaining({
        callId: "list-1",
        toolName: "list_sources",
        arguments: {},
      }),
      expect.objectContaining({
        callId: "read-1",
        toolName: "read_source",
        arguments: { path: "radcliffe-demo.md" },
      }),
    ]);
    expect(events.filter((event) => event.type === "tool_end")).toHaveLength(2);
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
