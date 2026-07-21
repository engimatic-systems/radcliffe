import { describe, expect, it } from "vitest";

import { splitCompletedSpeechSegments, spokenOutputFor } from "../src/spoken-output.js";

describe("spoken output policy", () => {
  it.each([
    [
      "hash",
      "The checksum is aabbccddeeff00112233445566778899.",
      "The checksum is a hash shown in the conversation.",
    ],
    [
      "identifier",
      "The job is job_0123456789abcdef.",
      "The job is an internal identifier shown in the conversation.",
    ],
    [
      "identifier",
      "Use 123e4567-e89b-12d3-a456-426614174000.",
      "Use an identifier shown in the conversation.",
    ],
    [
      "url",
      "See https://docs.example.test/guides/start?q=1.",
      "See a link on docs dot example dot test",
    ],
    [
      "path",
      "The result is at /srv/radcliffe/output/report.json.",
      "The result is at a file path shown in the conversation",
    ],
    [
      "timestamp",
      "It completed at 2026-07-21T04:00:00Z.",
      "It completed at Jul 21, 2026, 4:00 AM.",
    ],
    [
      "serialized_data",
      'The payload is {"ready":true,"count":2}.',
      "The payload is structured data available in the conversation.",
    ],
    [
      "long_number",
      "The counter is 123456789012345.",
      "The counter is a long number shown in the conversation.",
    ],
    ["code", "Run `npm run check` next.", "Run inline code shown in the conversation next."],
    ["citation", "That result is established [12].", "That result is established a citation."],
    ["repeated_punctuation", "Done!!!!!", "Done!"],
    [
      "acronym_metadata",
      "Metadata: HTTP UUID SHA256 JSON READY.",
      "Metadata: technical metadata shown in the conversation.",
    ],
  ])("replaces unrequested %s detail with a speakable alternative", (category, raw, expected) => {
    const result = spokenOutputFor(raw);
    expect(result.categories).toContain(category);
    expect(result.text).toContain(expected);
  });

  it("summarizes stack traces and fenced code without speaking their internals", () => {
    const stack = spokenOutputFor("Failure.\n at worker (/srv/app.ts:12:4)\n");
    expect(stack.categories).toContain("stack_trace");
    expect(stack.text).not.toContain("worker");
    const code = spokenOutputFor("Example:\n```ts\nconst secret = value;\n```\n");
    expect(code.categories).toContain("code");
    expect(code.text).toContain("a code sample");
    expect(code.text).not.toContain("const secret");
  });

  it("preserves explicit exact output byte for byte", () => {
    const raw = "https://example.test/a?x=1 job_0123456789abcdef";
    expect(spokenOutputFor(raw, true)).toEqual({ text: raw, categories: [] });
  });

  it("holds incomplete provider chunks until a safe sentence boundary", () => {
    expect(splitCompletedSpeechSegments("See https://example", false)).toEqual({
      segments: [],
      remainder: "See https://example",
    });
    expect(splitCompletedSpeechSegments("First sentence. Second", false)).toEqual({
      segments: ["First sentence. "],
      remainder: "Second",
    });
    expect(splitCompletedSpeechSegments("```ts\nvalue();\n```\nNext", false)).toEqual({
      segments: [],
      remainder: "```ts\nvalue();\n```\nNext",
    });
  });
});
