import { describe, expect, it } from "vitest";

import { SIDECAR_VERSION } from "../src/index.js";

describe("agent sidecar", () => {
  it("exposes its version", () => {
    expect(SIDECAR_VERSION).toBe("0.1.0");
  });
});
