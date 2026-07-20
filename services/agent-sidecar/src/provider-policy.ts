const LOCAL_PROVIDERS = new Set(["ollama"]);

export const ANTHROPIC_DEBUG_MODEL_ID = "claude-haiku-4-5-20251001";
export const ANTHROPIC_DEBUG_PRICING_BASIS =
  "anthropic/claude-haiku-4-5-20251001/usd-2026-05-27";
export const ANTHROPIC_DEBUG_CONTEXT_WINDOW = 200_000;
export const ANTHROPIC_DEBUG_PRICING = Object.freeze({
  input: 1,
  output: 5,
  cacheRead: 0.1,
  cacheWrite: 1.25,
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
      `Anthropic debug runs require pinned model ${ANTHROPIC_DEBUG_MODEL_ID}; ` +
        "a production model or router requires a new reviewed profile",
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
