import { describe, expect, it } from "vitest";

import {
  AGENT_CONTROL_ACTION_WIRE_IDS,
  isAgentControlRequest,
} from "../src/protocol.js";

function deliveryRequest() {
  return {
    schemaVersion: 1,
    requestId: "request-1",
    sessionId: "session-1",
    generation: 3,
    operationId: "operation-1",
    cancellationId: "cancellation-1",
    traceId: `trc_${"a".repeat(32)}`,
    requestSequence: 1,
    idempotencyKey: "idempotency-1",
    origin: {
      source: "call",
      actorId: "agent-1",
      sourceMessageId: "message-1",
      toolCallId: "call-1",
    },
    target: {
      scope: "snippet",
      sessionId: "session-1",
      turnId: "turn-1",
      snippetId: "snippet-1",
    },
    confirmation: "none",
    actionId: "speech_delivery.set_rate",
    arguments: { rate: "1.25x" },
  };
}

describe("agent-control contract", () => {
  it("maps stable action names to the protobuf wire enum", () => {
    expect(AGENT_CONTROL_ACTION_WIRE_IDS).toEqual({
      "speech_delivery.set_rate": 1,
      "configuration.set": 2,
      "session.create": 3,
      "session.switch": 4,
      "session.restart": 5,
    });
    expect(isAgentControlRequest(deliveryRequest())).toBe(true);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        arguments: { rate: 1.333 },
      }),
    ).toBe(true);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        arguments: { rate: 1.501 },
      }),
    ).toBe(false);
  });

  it("rejects unknown actions and arbitrary settings", () => {
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        actionId: "process.execute",
        arguments: { command: "whoami" },
      }),
    ).toBe(false);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        actionId: "configuration.set",
        target: { scope: "session", sessionId: "session-1" },
        arguments: { key: "api_key", value: "secret" },
      }),
    ).toBe(false);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        arguments: { rate: "1.0x", path: "/tmp" },
      }),
    ).toBe(false);
  });

  it("enforces target shape and the confirmation matrix", () => {
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        actionId: "configuration.set",
        target: { scope: "device", sessionId: "session-1", deviceId: "pixel-1" },
        confirmation: "user",
        arguments: { key: "response_detail", value: "brief" },
      }),
    ).toBe(true);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        actionId: "configuration.set",
        target: { scope: "device", sessionId: "session-1", deviceId: "pixel-1" },
        confirmation: "none",
        arguments: { key: "response_detail", value: "brief" },
      }),
    ).toBe(false);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        actionId: "session.switch",
        target: { scope: "session", sessionId: "session-1" },
        confirmation: "user",
        arguments: { destinationSessionId: "session-1" },
      }),
    ).toBe(false);
  });

  it("keeps user model call and text-source origins distinct", () => {
    for (const source of ["user", "model", "text_source"]) {
      expect(
        isAgentControlRequest({
          ...deliveryRequest(),
          origin: {
            source,
            actorId: "actor-1",
            sourceMessageId: "message-1",
          },
        }),
      ).toBe(true);
    }
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        origin: {
          source: "model",
          actorId: "actor-1",
          sourceMessageId: "message-1",
          toolCallId: "must-not-be-present",
        },
      }),
    ).toBe(false);
  });

  it("fails closed for forward action IDs while permitting strict additive envelopes", () => {
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        actionId: "future.action",
        arguments: {},
      }),
    ).toBe(false);
    expect(
      isAgentControlRequest({
        ...deliveryRequest(),
        futureField: "ignored by a future schema",
      }),
    ).toBe(false);
  });
});
