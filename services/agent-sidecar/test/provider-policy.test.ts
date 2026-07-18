import { describe, expect, it } from "vitest";

import { assertProviderAllowed } from "../src/provider-policy.js";

describe("model provider cost guard", () => {
  it("allows the local provider without an opt-in", () => {
    expect(() => assertProviderAllowed("ollama", undefined)).not.toThrow();
  });

  it.each(["openai", "anthropic"])("blocks %s unless the user-approved flag is set", (provider) => {
    expect(() => assertProviderAllowed(provider, undefined)).toThrow(/cloud model provider/);
    expect(() => assertProviderAllowed(provider, "0")).toThrow(/cloud model provider/);
    expect(() => assertProviderAllowed(provider, "1")).not.toThrow();
  });
});
