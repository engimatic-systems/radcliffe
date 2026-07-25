const LOCAL_PROVIDERS = new Set(["ollama"]);

export const ANTHROPIC_DEBUG_MODEL_ID = "claude-sonnet-5";
export const ANTHROPIC_DEBUG_PRICING_BASIS = "anthropic/claude-sonnet-5/usd-2026-07-25";
export const ANTHROPIC_DEBUG_CONTEXT_WINDOW = 1_000_000;
// Mirrors the provider registry exactly so the contract assertion stays a real tripwire.
// These are Sonnet 5's promotional rates, which end 2026-08-31; standard rates are $3/$15.
// The assertion is expected to fire when the registry moves to standard pricing — that is
// the signal to re-review the budget basis, not a bug.
export const ANTHROPIC_DEBUG_PRICING = Object.freeze({
  input: 2,
  output: 10,
  cacheRead: 0.2,
  cacheWrite: 2.5,
});

export function assertProviderAllowed(provider: string, cloudOptIn: string | undefined): void {
  if (!LOCAL_PROVIDERS.has(provider) && cloudOptIn !== "1") {
    throw new Error(
      `cloud model provider ${provider} is disabled; ` +
        "set RADCLIFFE_ALLOW_CLOUD_MODELS=1 only for a user-approved test run",
    );
  }
}

export function assertSupportedModelProfile(provider: string, modelId: string): void {
  if (provider === "ollama") return;
  if (provider !== "anthropic") {
    throw new Error(`cloud model provider ${provider} has no bounded Radcliffe profile`);
  }
  if (modelId !== ANTHROPIC_DEBUG_MODEL_ID) {
    throw new Error(
      `Anthropic runs require pinned model ${ANTHROPIC_DEBUG_MODEL_ID}; ` +
        "a different model or a router requires a new reviewed profile",
    );
  }
}

export function assertAnthropicDebugModelContract(model: {
  contextWindow: number;
  cost: { input: number; output: number; cacheRead: number; cacheWrite: number };
}): void {
  if (
    model.contextWindow !== ANTHROPIC_DEBUG_CONTEXT_WINDOW ||
    Object.entries(ANTHROPIC_DEBUG_PRICING).some(
      ([key, value]) => model.cost[key as keyof typeof model.cost] !== value,
    )
  ) {
    throw new Error("pinned Anthropic model contract changed; review pricing and context limits");
  }
}

export function takeEnvironmentSecret(
  environment: Record<string, string | undefined>,
  name: string,
): string | undefined {
  const value = environment[name];
  delete environment[name];
  return value;
}
