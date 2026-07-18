const LOCAL_PROVIDERS = new Set(["ollama"]);

export function assertProviderAllowed(provider: string, cloudOptIn: string | undefined): void {
  if (!LOCAL_PROVIDERS.has(provider) && cloudOptIn !== "1") {
    throw new Error(
      `cloud model provider ${provider} is disabled; ` +
        "set RADCLIFFE_ALLOW_CLOUD_MODELS=1 only for a user-approved test run",
    );
  }
}
