import { describe, expect, it } from "vitest";

import {
  ANTHROPIC_DEBUG_MODEL_ID,
  assertAnthropicDebugModelContract,
  assertProviderAllowed,
  assertSupportedModelProfile,
  takeEnvironmentSecret,
} from "../src/provider-policy.js";

describe("model provider cost guard", () => {
  it("allows the local provider without an opt-in", () => {
    expect(() => assertProviderAllowed("ollama", undefined)).not.toThrow();
  });

  it.each(["openai", "anthropic"])("blocks %s unless the user-approved flag is set", (provider) => {
    expect(() => assertProviderAllowed(provider, undefined)).toThrow(/cloud model provider/);
    expect(() => assertProviderAllowed(provider, "0")).toThrow(/cloud model provider/);
    expect(() => assertProviderAllowed(provider, "1")).not.toThrow();
  });

  it("requires the pinned bounded Anthropic debug model", () => {
    expect(() => assertSupportedModelProfile("anthropic", ANTHROPIC_DEBUG_MODEL_ID)).not.toThrow();
    expect(() => assertSupportedModelProfile("anthropic", "claude-haiku-4-5")).toThrow(
      /pinned model/,
    );
    expect(() => assertSupportedModelProfile("openai", "anything")).toThrow(
      /no bounded Radcliffe profile/,
    );
    expect(() => assertSupportedModelProfile("ollama", "qwen3:8b")).not.toThrow();
  });

  it("captures a provider key once and removes it from the process environment", () => {
    const environment = { ANTHROPIC_API_KEY: "fake-provider-key", SAFE_VALUE: "retained" };

    const captured = takeEnvironmentSecret(environment, "ANTHROPIC_API_KEY");

    expect(captured).toBe("fake-provider-key");
    expect(environment).toEqual({ SAFE_VALUE: "retained" });
  });

  it("fails closed if the pinned model pricing or context contract drifts", () => {
    const reviewed = {
      contextWindow: 1_000_000,
      cost: { input: 2, output: 10, cacheRead: 0.2, cacheWrite: 2.5 },
    };

    expect(() => assertAnthropicDebugModelContract(reviewed)).not.toThrow();
    expect(() =>
      assertAnthropicDebugModelContract({
        ...reviewed,
        cost: { ...reviewed.cost, output: 6 },
      }),
    ).toThrow(/contract changed/);
  });
});
