import {
  createFauxCore,
  fauxAssistantMessage,
  fauxToolCall,
} from "@earendil-works/pi-ai/providers/faux";
import { describe, expect, it, vi } from "vitest";

import {
  classifyDeliveryIntent,
  classifyExactOutputIntent,
  CONFIGURE_PARAMETERS,
  containsTextualToolProtocol,
  convertRadcliffeContext,
  explicitJobLaunchIntent,
  explicitJobManagementOperation,
  finalizeExactOutput,
  MANAGE_JOB_PARAMETERS,
  MANAGE_SESSION_PARAMETERS,
  modelContextFromRequest,
  PiAgentOrchestrator,
  prepareConfigureArguments,
  prepareManageJobArguments,
  prepareManageSessionArguments,
  prepareReadJobSourceArguments,
  prepareRunJobArguments,
  prepareReadSourceArguments,
  READ_JOB_SOURCE_PARAMETERS,
  RADCLIFFE_SYSTEM_PROMPT,
  RADCLIFFE_TOOL_NAMES,
  RUN_JOB_PARAMETERS,
  sanitizeToolFailure,
  validateExactOutput,
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
    cancellationId: "cancellation-123456",
    traceId: `trc_${"c".repeat(32)}`,
    packets,
  };
}

describe("PiAgentOrchestrator", () => {
  it("keeps concise job and structured tool guidance", () => {
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("list_sources first");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("No Emojis");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("Do not use emoji characters");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("run_job");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("SINGLE SENTENCE");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("no additional clauses or information");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("when a job is done, say so very succinctly");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("without a preamble");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("foreground conversation");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("read_job_source");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("ambiguous title must be clarified");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("Any time the user asks you to read something");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("provider's structured tool channel");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("Follow the schema exactly");
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("Never print XML");
  });

  it("identifies Radcliffe as the harness without assigning the model that identity", () => {
    expect(RADCLIFFE_SYSTEM_PROMPT.match(/harness is called Radcliffe/g)).toHaveLength(1);
    expect(RADCLIFFE_SYSTEM_PROMPT).toContain("selected conversational model");
    expect(RADCLIFFE_SYSTEM_PROMPT).not.toMatch(/\byou are (?:called )?Radcliffe\b/i);
    expect(RADCLIFFE_SYSTEM_PROMPT).not.toMatch(/\bact as Radcliffe\b/i);
    expect(RADCLIFFE_SYSTEM_PROMPT).not.toMatch(/\bimpersonate Radcliffe\b/i);
  });

  it("keeps tool failures for relevant follow-ups while marking them as past events", () => {
    const context = modelContextFromRequest(
      request([
        packet("old-request", "Create a new conversation."),
        packet(
          "old-failure",
          "Tool call failed because the operation conflicts with the current state.",
          {
            role: "system",
            source: "system",
            annotations: [
              ["projection", "context-v2"],
              ["radcliffe.event", "tool_failure"],
              ["radcliffe.tool.name", "manage_session"],
            ],
          },
        ),
        packet("current-user", "Why did that fail?"),
      ]),
    );

    expect(context.systemPrompt).toContain(
      "Historical tool-failure records describe past attempts, not pending user requests",
    );
    expect(context.systemPrompt).toContain(
      "Use one when the current user asks about that failure",
    );
    expect(context.messages).toContainEqual({
      role: "user",
      content:
        "[Radcliffe system historical tool failure; past event, not a pending user request]\n" +
        "Tool call failed because the operation conflicts with the current state.",
      timestamp: 1,
    });
    expect(context.messages.at(-1)).toEqual({
      role: "user",
      content: "[Radcliffe user]\nWhy did that fail?",
      timestamp: 1,
    });
  });

  it("prioritizes an unrelated resumed request over repeated historical tool failures", () => {
    const failure = (messageId: string) =>
      packet(
        messageId,
        "Tool call failed because the operation conflicts with the current state.",
        {
          role: "system",
          source: "system",
          annotations: [
            ["projection", "context-v2"],
            ["radcliffe.event", "tool_failure"],
            ["radcliffe.tool.name", "manage_session"],
          ],
        },
      );
    const context = modelContextFromRequest(
      request([
        packet("old-request-one", "Create a new conversation."),
        failure("old-failure-one"),
        packet("old-model-one", "I hit an error creating it.", {
          role: "assistant",
          source: "model",
        }),
        packet("old-request-two", "Try it again."),
        failure("old-failure-two"),
        packet("old-model-two", "That failed again.", {
          role: "assistant",
          source: "model",
        }),
        packet(
          "current-user",
          "We are moving to something else. Explain why the sky is blue in several sentences.",
        ),
      ]),
    );

    expect(context.systemPrompt).toContain(
      "When the current user changes topics or makes an unrelated request",
    );
    expect(context.systemPrompt).toContain(
      "answer the current request directly and do not repeat or explain the old failure",
    );
    expect(
      context.messages.filter(
        (message) =>
          message.role === "user" &&
          typeof message.content === "string" &&
          message.content.includes("historical tool failure; past event"),
      ),
    ).toHaveLength(2);
    expect(context.messages.at(-1)).toEqual({
      role: "user",
      content:
        "[Radcliffe user]\n" +
        "We are moving to something else. Explain why the sky is blue in several sentences.",
      timestamp: 1,
    });
  });

  it("recognizes explicit job management while leaving how-to questions non-mutating", () => {
    expect(
      explicitJobManagementOperation(
        "Okay, now go ahead and cancel that job. This tests cancellation.",
      ),
    ).toBe("cancel");
    expect(explicitJobManagementOperation("Report the progress on that job.")).toBe("status");
    expect(explicitJobManagementOperation("Make the background job high priority.")).toBe(
      "set_priority",
    );
    expect(explicitJobManagementOperation("Add this as a follow-up to the job.")).toBe(
      "follow_up",
    );
    expect(explicitJobManagementOperation("Can you just retry it?")).toBe("retry");
    expect(explicitJobManagementOperation("How can I retry that job?")).toBeUndefined();
    expect(explicitJobManagementOperation("How can I cancel that job?")).toBeUndefined();
    expect(explicitJobManagementOperation("Stop talking about it.")).toBeUndefined();
  });

  it("recognizes explicit job launches and textual tool protocol", () => {
    expect(explicitJobLaunchIntent("Start a background job to download the page.")).toBe(true);
    expect(explicitJobLaunchIntent("Run a job that downloads the first result.")).toBe(true);
    expect(explicitJobLaunchIntent("Start a new job and save it to disk.")).toBe(true);
    expect(explicitJobLaunchIntent("Kick off a batchground task for this.")).toBe(true);
    expect(explicitJobLaunchIntent("How can I start a background job?")).toBe(false);
    expect(explicitJobLaunchIntent("Tell me how to run a job.")).toBe(false);
    expect(containsTextualToolProtocol("<function_calls>\n<invoke name=\"run_job\">"))
      .toBe(true);
    expect(containsTextualToolProtocol("I'll use <invoke name=\"run_job\"> now.")).toBe(true);
    expect(
      containsTextualToolProtocol(
        "I'll launch it.\n```\nrun_job\njob_type: download\ntarget_url: https://example.test\n```",
      ),
    ).toBe(true);
    expect(containsTextualToolProtocol("The phrase function call is ordinary prose.")).toBe(false);
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
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "usage",
        requestId: "request-1",
        provider: "radcliffe-test",
      }),
    );
    expect(events.at(-1)).toEqual({ type: "complete", requestId: "request-1" });
    expect(tool).not.toHaveBeenCalled();
  });

  it("discards textual tool markup and retries through the structured channel", async () => {
    const faux = createFauxCore({ provider: "radcliffe-tool-protocol-test" });
    const leaked =
      "I'll launch it now.\n```\nrun_job\njob_type: download\n" +
      "target_url: https://example.test/page\noutput_format: markdown\n```";
    faux.setResponses([
      fauxAssistantMessage(leaked),
      fauxAssistantMessage(
        fauxToolCall("run_job", { instruction: "Download the page" }, { id: "retry-run-job" }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("I started it."),
    ]);
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"queued"}',
      details: { jobId: "job-1" },
    }));
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(request([packet("user-1", "Download the requested page for me.")]), (event) => {
      events.push(event);
    });

    expect(tool).toHaveBeenCalledTimes(1);
    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({ toolName: "run_job", callId: "retry-run-job" }),
      expect.anything(),
      expect.any(Function),
    );
    const rendered = JSON.stringify(
      events.filter((event) => event.type === "model_delta" || event.type === "model_complete"),
    );
    expect(rendered).not.toContain("run_job");
    expect(rendered).not.toContain("job_type");
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I started it.",
    });
  });

  it("quarantines a textual tool imitation when later source delivery is requested verbatim", async () => {
    const faux = createFauxCore({ provider: "radcliffe-verbatim-tool-protocol-test" });
    const utterance =
      "Download the first commentary on the Gettysburg Address and then read it to me verbatim.";
    faux.setResponses([
      fauxAssistantMessage(
        'I will download it.\nrun_job(tool_type: "download", resource: "commentary")',
      ),
      fauxAssistantMessage(
        fauxToolCall(
          "run_job",
          { instruction: "Download the first commentary on the Gettysburg Address." },
          { id: "verbatim-retry-run-job" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("I started it."),
    ]);
    const tool = vi.fn(async () => ({ modelContent: "accepted", details: { jobId: "job-1" } }));
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(request([packet("user-1", utterance)]), (event) => {
      events.push(event);
    });

    expect(classifyExactOutputIntent(utterance)).toBeUndefined();
    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({ toolName: "run_job", callId: "verbatim-retry-run-job" }),
      expect.anything(),
      expect.any(Function),
    );
    expect(JSON.stringify(events)).not.toContain("tool_type");
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I started it.",
    });
  });

  it("forces a structured run_job call for an explicit background-job launch", async () => {
    const faux = createFauxCore({ provider: "radcliffe-required-run-job-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("run_job", { instruction: "Download the page" }, { id: "run-job-1" }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("I started it."),
    ]);
    const stream = vi.fn(faux.streamSimple);
    const model = { ...faux.getModel(), api: "anthropic-messages" as const };
    const orchestrator = new PiAgentOrchestrator(
      model,
      stream,
      vi.fn(async () => ({ modelContent: "accepted", details: { jobId: "job-1" } })),
    );

    await orchestrator.run(
      request([packet("user-1", "Start a background job to download the page.")]),
      () => undefined,
    );

    expect(stream.mock.calls[0]?.[2]).toMatchObject({
      toolChoice: { type: "tool", name: "run_job" },
    });
    expect(stream.mock.calls[0]?.[1].tools?.map((tool) => tool.name)).toEqual(
      RADCLIFFE_TOOL_NAMES,
    );
  });

  it("routes a retained interrupted job request together with its one-word addendum", async () => {
    const faux = createFauxCore({ provider: "radcliffe-interrupted-user-context-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "run_job",
          {
            instruction:
              "Download the Gettysburg Address and save only its verbatim text as Markdown.",
          },
          { id: "retained-run-job" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("Working on it."),
    ]);
    const stream = vi.fn(faux.streamSimple);
    const tool = vi.fn(async () => ({
      modelContent: "accepted",
      details: { jobId: "job-1" },
    }));
    const model = { ...faux.getModel(), api: "anthropic-messages" as const };
    const orchestrator = new PiAgentOrchestrator(model, stream, tool);
    const first =
      "Start a background job to get the Gettysburg Address and save it as Markdown.";
    const userMessage = `${first}\nVerbatim.`;
    const turn = request([
      packet("prior-user", first, {
        annotations: [
          ["radcliffe.turn_disposition", "interrupted"],
          ["radcliffe.turn_id", "turn-prior"],
        ],
      }),
      packet("current-user", "Verbatim.", {
        annotations: [["radcliffe.turn_id", "turn-current"]],
      }),
    ]);
    turn.userMessage = userMessage;

    await orchestrator.run(turn, () => undefined);

    const modelMessages = stream.mock.calls[0]?.[1].messages;
    expect(modelMessages).toEqual([
      {
        role: "user",
        content: `[Radcliffe user]\n${first}`,
        timestamp: 1,
      },
      {
        role: "user",
        content: "[Radcliffe user]\nVerbatim.",
        timestamp: 1,
      },
    ]);
    expect(JSON.stringify(modelMessages).match(/Gettysburg Address/g)).toHaveLength(1);
    expect(stream.mock.calls[0]?.[2]).toMatchObject({
      toolChoice: { type: "tool", name: "run_job" },
    });
    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "run_job",
        userMessage,
        arguments: {
          instruction:
            "Download the Gettysburg Address and save only its verbatim text as Markdown.",
        },
      }),
      expect.anything(),
      expect.any(Function),
    );
  });

  it("sends every buffered user fragment to the model exactly once in chronological order", () => {
    const fragments = [
      "This is a test of the Gettysburg Address.",
      "Four score and seven years ago.",
      "Now we are engaged in a great civil war.",
      "What did I say?",
    ];
    const turn = request(
      fragments.map((content, index) =>
        packet(`user-${index + 1}`, content, {
          annotations: [
            [
              "radcliffe.turn_disposition",
              index === fragments.length - 1 ? "completed" : "interrupted",
            ],
            ["radcliffe.turn_id", `turn-${index + 1}`],
          ],
          timestamp: index + 1,
        }),
      ),
    );
    turn.userMessage = fragments.join("\n");

    const context = modelContextFromRequest(turn);

    expect(context.messages).toEqual(
      fragments.map((content, index) => ({
        role: "user",
        content: `[Radcliffe user]\n${content}`,
        timestamp: index + 1,
      })),
    );
    for (const fragment of fragments) {
      expect(
        context.messages.filter(
          (message) =>
            message.role === "user" &&
            typeof message.content === "string" &&
            message.content.includes(fragment),
        ),
      ).toHaveLength(1);
    }
  });

  it("keeps every schema available when a first-turn job request mentions later verbatim delivery", async () => {
    const faux = createFauxCore({ provider: "radcliffe-first-turn-schema-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "run_job",
          { instruction: "Download the first commentary and save it to disk." },
          { id: "run-job-first-turn" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("I started it."),
    ]);
    const stream = vi.fn(faux.streamSimple);
    const model = { ...faux.getModel(), api: "anthropic-messages" as const };
    const tool = vi.fn(async () => ({ modelContent: "accepted", details: { jobId: "job-1" } }));
    const orchestrator = new PiAgentOrchestrator(model, stream, tool);
    const utterance =
      "Start a new job that downloads the first piece of commentary, save it to disk, " +
      "and after it is done I will have you read it verbatim.";

    await orchestrator.run(request([packet("first-user", utterance)]), () => undefined);

    expect(classifyExactOutputIntent(utterance)).toBeUndefined();
    expect(stream.mock.calls[0]?.[1].tools?.map((candidate) => candidate.name)).toEqual(
      RADCLIFFE_TOOL_NAMES,
    );
    expect(stream.mock.calls[0]?.[2]).toMatchObject({
      toolChoice: { type: "tool", name: "run_job" },
    });
    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({ toolName: "run_job", callId: "run-job-first-turn" }),
      expect.anything(),
      expect.any(Function),
    );
  });

  it("preserves textual tool tags when the user explicitly requests them verbatim", async () => {
    const faux = createFauxCore({ provider: "radcliffe-exact-tool-tag-test" });
    faux.setResponses([fauxAssistantMessage("I would normally paraphrase this.")]);
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, vi.fn());

    await orchestrator.run(
      request([packet("exact-tag", 'Repeat exactly: "<function_calls>"')]),
      (event) => {
        events.push(event);
      },
    );

    expect(events.filter((event) => event.type === "model_delta")).toEqual([
      { type: "model_delta", requestId: "request-1", text: "<function_calls>" },
    ]);
  });

  it("keeps machine detail in display text while emitting a speakable alternative", async () => {
    const faux = createFauxCore({ provider: "radcliffe-spoken-output-test" });
    const raw =
      "The job job_0123456789abcdef wrote /srv/radcliffe/output/report.json. " +
      "Details are at https://docs.example.test/jobs/0123456789.";
    faux.setResponses([fauxAssistantMessage(raw)]);
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, vi.fn());

    await orchestrator.run(request([packet("spoken-output", "Summarize the result")]), (event) => {
      events.push(event);
    });

    const deltas = events.filter((event) => event.type === "model_delta");
    expect(deltas.map((event) => ("text" in event ? event.text : "")).join("")).toBe(raw);
    const spoken = deltas
      .map((event) => (event.type === "model_delta" ? (event.spokenText ?? event.text) : ""))
      .join("");
    expect(spoken).not.toContain("job_0123456789abcdef");
    expect(spoken).not.toContain("/srv/radcliffe/output/report.json");
    expect(spoken).not.toContain("https://");
    expect(deltas.flatMap((event) => (event.type === "model_delta" ? (event.spokenCategories ?? []) : []))).toEqual(
      expect.arrayContaining(["identifier", "path", "url"]),
    );
    expect(events.find((event) => event.type === "model_complete")).toMatchObject({ text: raw });
  });

  it("surfaces a provider budget failure as a visible sidecar error", async () => {
    const faux = createFauxCore({ provider: "radcliffe-budget-test" });
    faux.setResponses([
      fauxAssistantMessage("", {
        stopReason: "error",
        errorMessage: "cloud session budget exhausted",
      }),
    ]);
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, vi.fn());

    await expect(
      orchestrator.run(request([packet("current", "Hello")]), (event) => {
        events.push(event);
      }),
    ).rejects.toThrow("cloud session budget exhausted");
    expect(events.at(-1)).toEqual({
      type: "error",
      requestId: "request-1",
      message: "cloud session budget exhausted",
      code: "cloud_budget_exhausted",
    });
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
      arguments: { path: "demo.md", rate: "1.25x" },
    });
    expect(events.map((event) => event.type)).toEqual(
      expect.arrayContaining(["tool_start", "tool_update", "tool_end", "model_complete"]),
    );
    expect(events.find((event) => event.type === "tool_end")).toMatchObject({
      result: { details: { sourceEnvelopeProtoBase64: sourceEnvelope } },
    });
  });

  it("routes a typed allowlisted configuration change with exact turn identity", async () => {
    const faux = createFauxCore({ provider: "radcliffe-configuration-test" });
    faux.setResponses([
      (context) => {
        const toolNames = (context.tools ?? []).map((candidate) => candidate.name);
        expect(toolNames).not.toContain("configure");
        expect(toolNames).not.toContain("read_source");
        expect(context.systemPrompt).toContain("already applied for this session");
        return fauxAssistantMessage("Future speech will use the faster pace.");
      },
    ]);
    const tool = vi.fn(async () => ({
      modelContent:
        '{"effective_scope":"session","key":"default_speech_rate","value":"1.25x"}',
      details: { mutationId: "configuration:configure-1" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet("user-1", "Speak faster from now on", {
          annotations: [
            ["projection", "context-v2"],
            ["radcliffe.turn_id", "turn-1"],
          ],
        }),
      ]),
      () => undefined,
    );

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        cancellationId: "cancellation-123456",
        turnId: "turn-1",
        toolName: "configure",
        arguments: {
          operation: "set",
          key: "default_speech_rate",
          scope: "session",
          value: "1.25x",
        },
      }),
      undefined,
    );
  });

  it.each([
    ["Count slowly from 1 to 60", { rate: "0.75x", scope: "turn", sourceRead: false }],
    ["Answer faster this time", { rate: "1.25x", scope: "turn", sourceRead: false }],
    [
      "Speak at 0.75x for this session",
      { rate: "0.75x", scope: "session", sourceRead: false },
    ],
    [
      "Use the normal speech rate from now on",
      { rate: "1.0x", scope: "session", sourceRead: false },
    ],
    ["Read demo.md slowly", { rate: "0.75x", scope: "turn", sourceRead: true }],
  ])("classifies delivery intent: %s", (text, expected) => {
    expect(classifyDeliveryIntent(text)).toEqual(expected);
  });

  it("leaves ordinary mentions of speed out of delivery routing", () => {
    expect(classifyDeliveryIntent("The faster algorithm won the benchmark.")).toBeUndefined();
    expect(classifyDeliveryIntent("Count from 1 to 60")).toBeUndefined();
  });

  it("classifies numeric ranges and exact machine-readable values", () => {
    expect(classifyExactOutputIntent("Count slowly from 1 to 60")).toEqual({
      kind: "numbers",
      sequence: { start: 1, end: 60 },
    });
    expect(classifyExactOutputIntent('Repeat the ID exactly: "build-A7:42"')).toEqual({
      kind: "identifier",
      literal: "build-A7:42",
    });
    expect(
      classifyExactOutputIntent("Say this hash exactly: aabbccddeeff00112233445566778899"),
    ).toEqual({
      kind: "hash",
      literal: "aabbccddeeff00112233445566778899",
    });
    expect(classifyExactOutputIntent("Read exactly https://example.test/a?q=1")).toEqual({
      kind: "url",
      literal: "https://example.test/a?q=1",
    });
    expect(classifyExactOutputIntent('Repeat this JSON exactly: {"ready":true}')).toEqual({
      kind: "json",
      literal: '{"ready":true}',
    });
    expect(classifyExactOutputIntent("Say this path exactly: /srv/radcliffe/config.json")).toEqual({
      kind: "path",
      literal: "/srv/radcliffe/config.json",
    });
    expect(classifyExactOutputIntent("Repeat exactly 2026-07-21T04:00:00Z")).toEqual({
      kind: "timestamp",
      literal: "2026-07-21T04:00:00Z",
    });
    expect(classifyExactOutputIntent('Spell out "Q7-x" character by character')).toEqual({
      kind: "characters",
      literal: "Q7-x",
    });
    expect(classifyExactOutputIntent('Repeat exactly: "No compression allowed"')).toEqual({
      kind: "literal",
      literal: "No compression allowed",
    });
  });

  it("does not substitute explicitly requested exact machine output", async () => {
    const faux = createFauxCore({ provider: "radcliffe-exact-machine-output-test" });
    const literal = "https://example.test/a?q=1";
    faux.setResponses([fauxAssistantMessage("I would normally add a preamble.")]);
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, vi.fn());

    await orchestrator.run(
      request([packet("exact-url", `Repeat exactly ${literal}`)]),
      (event) => {
        events.push(event);
      },
    );

    expect(events.filter((event) => event.type === "model_delta")).toEqual([
      { type: "model_delta", requestId: "request-1", text: literal },
    ]);
  });

  it("retains startup schemas while disabling tool execution for true exact output", async () => {
    const faux = createFauxCore({ provider: "radcliffe-exact-schema-test" });
    faux.setResponses([
      (context) => {
        expect(context.tools?.map((candidate) => candidate.name)).toEqual(RADCLIFFE_TOOL_NAMES);
        return fauxAssistantMessage("I would normally add a preamble.");
      },
    ]);
    const stream = vi.fn(faux.streamSimple);
    const model = { ...faux.getModel(), api: "anthropic-messages" as const };
    const tool = vi.fn();
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(model, stream, tool);

    await orchestrator.run(
      request([packet("exact-url", "Repeat exactly https://example.test/a?q=1")]),
      (event) => {
        events.push(event);
      },
    );

    expect(stream.mock.calls[0]?.[2]).toMatchObject({ toolChoice: "none" });
    expect(tool).not.toHaveBeenCalled();
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "https://example.test/a?q=1",
    });
  });

  it.each([
    ["1, 2, 4, 5.", "missing"],
    ["1, 2, 2, 3, 4, 5.", "duplicated_or_extra"],
    ["1, 3, 2, 4, 5.", "reordered_or_replaced"],
    ["1, 2, 3... 5.", "abbreviated"],
  ])("rejects invalid numeric exact output: %s", (candidate, reason) => {
    const validation = validateExactOutput(
      { kind: "numbers", sequence: { start: 1, end: 5 } },
      candidate,
    );
    expect(validation.valid).toBe(false);
    expect(validation.reasons).toContain(reason);
  });

  it("repairs an abbreviated number range into a complete bounded sequence", () => {
    const completed = finalizeExactOutput(
      { kind: "numbers", sequence: { start: 1, end: 60 } },
      "1, 2, 3... 60.",
    );
    expect([...completed.matchAll(/\b\d+\b/g)].map((match) => Number(match[0]))).toEqual(
      Array.from({ length: 60 }, (_, index) => index + 1),
    );
    expect(completed).not.toContain("...");
  });

  it("derives continuation from the last durable emitted number", () => {
    const history = [
      packet("range-request", "Count from 1 to 1000"),
      packet("durable-output", "1, 2, 3, 4, 200.\nThe sequence stops here.", {
        role: "assistant",
        source: "model",
      }),
    ];
    expect(classifyExactOutputIntent("Keep going", history)).toEqual({
      kind: "numbers",
      sequence: { start: 201, end: 1000 },
    });
  });

  it("stops truthfully at the bounded limit and continues from durable output", () => {
    const original = { kind: "numbers" as const, sequence: { start: 1, end: 250 } };
    const first = finalizeExactOutput(original, "1, 2, 3... 250.");
    const firstNumbers = [...first.matchAll(/\b\d+\b/g)].map((match) => Number(match[0]));
    expect(firstNumbers).toEqual(Array.from({ length: 200 }, (_, index) => index + 1));
    expect(first).toContain("stops at the last displayed number");

    const continuation = classifyExactOutputIntent("Keep going", [
      packet("long-range", "Count from 1 to 250"),
      packet("durable-first-part", first, { role: "assistant", source: "model" }),
    ]);
    expect(continuation).toEqual({
      kind: "numbers",
      sequence: { start: 201, end: 250 },
    });
    const second = finalizeExactOutput(continuation!, "201, 202... 250.");
    expect([...second.matchAll(/\b\d+\b/g)].map((match) => Number(match[0]))).toEqual(
      Array.from({ length: 50 }, (_, index) => index + 201),
    );
  });

  it.each(["brief", "standard", "detailed"] as const)(
    "buffers and repairs exact counting before emission under %s detail",
    async (responseDetail) => {
      const faux = createFauxCore({ provider: `radcliffe-exact-${responseDetail}-test` });
      faux.setResponses([
        (context) => {
          expect(context.systemPrompt).toContain("Fidelity constraint");
          expect(context.systemPrompt).toContain("overrides response-detail brevity");
          return fauxAssistantMessage("1, 2, 3... 60.");
        },
      ]);
      const events: SidecarEvent[] = [];
      const tool = vi.fn();
      const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

      await orchestrator.run(
        { ...request([packet("exact-count", "Count from 1 to 60")]), responseDetail },
        (event) => {
          events.push(event);
        },
      );

      expect(tool).not.toHaveBeenCalled();
      const deltas = events.filter((event) => event.type === "model_delta");
      expect(deltas).toHaveLength(1);
      const text = "text" in deltas[0]! ? deltas[0].text : "";
      expect([...text.matchAll(/\b\d+\b/g)].map((match) => Number(match[0]))).toEqual(
        Array.from({ length: 60 }, (_, index) => index + 1),
      );
      expect(events.find((event) => event.type === "model_complete")).toMatchObject({ text });
      expect(JSON.stringify(events)).not.toContain("1, 2, 3... 60");
    },
  );

  it("combines exact counting with one turn-scoped slow-delivery mutation", async () => {
    const faux = createFauxCore({ provider: "radcliffe-exact-slow-test" });
    faux.setResponses([fauxAssistantMessage("One, two, three, and so on.")]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn(async () => ({ modelContent: "configured", details: {} }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([packet("exact-slow", "Count slowly from 1 to 60")]),
      (event) => {
        events.push(event);
      },
    );

    expect(tool).toHaveBeenCalledOnce();
    expect(events.find((event) => event.type === "tool_start")).toMatchObject({
      toolName: "configure",
      arguments: {
        operation: "set",
        key: "default_speech_rate",
        scope: "turn",
        value: "0.75x",
      },
    });
    const complete = events.find((event) => event.type === "model_complete");
    const text = complete && "text" in complete ? complete.text : "";
    expect([...text.matchAll(/\b\d+\b/g)]).toHaveLength(60);
  });

  it("keeps named-source exact reading on the canonical source path", () => {
    const context = modelContextFromRequest(
      request([packet("source", "Read the file radcliffe-demo.md exactly")]),
    );
    expect(context.systemPrompt).not.toContain("Fidelity constraint:");
  });

  it("normalizes weak-model speech rate aliases before schema validation", () => {
    expect(
      prepareConfigureArguments({
        operation: "set",
        key: "default_speech_rate",
        scope: "turn",
        value: "slowly",
      }),
    ).toEqual({
      operation: "set",
      key: "default_speech_rate",
      scope: "turn",
      value: "0.75x",
    });
    expect(prepareReadSourceArguments({ path: "demo.md", rate: 1.5 })).toEqual({
      path: "demo.md",
      rate: "1.5x",
    });
  });

  it("accepts a start boundary and rejects one that cannot address a sentence", () => {
    expect(prepareReadSourceArguments({ path: "demo.md", startAtBoundary: 10 })).toEqual({
      path: "demo.md",
      startAtBoundary: 10,
    });
    // Local models routinely emit an integer-valued string for a numeric field.
    expect(prepareReadSourceArguments({ path: "demo.md", startAtBoundary: "10" })).toEqual({
      path: "demo.md",
      startAtBoundary: 10,
    });
    expect(prepareReadSourceArguments({ path: "demo.md" })).toEqual({ path: "demo.md" });
    expect(
      prepareReadJobSourceArguments({ reference: "nicotine.md", startAtBoundary: 3 }),
    ).toEqual({ reference: "nicotine.md", startAtBoundary: 3 });

    for (const invalid of [-1, 1.5, "later", "", true, null, 100_001]) {
      expect(() =>
        prepareReadSourceArguments({ path: "demo.md", startAtBoundary: invalid }),
      ).toThrow("tool_argument_invalid:read_source:start_at_boundary");
    }
  });

  it("emits one redacted recoverable diagnostic for a malformed model tool call", async () => {
    const faux = createFauxCore({ provider: "radcliffe-invalid-tool-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "configure",
          {
            operation: "set",
            key: "default_speech_rate",
            scope: "turn",
            value: "private-invalid-rate",
          },
          { id: "bad-configure" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("I kept the current rate."),
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn();
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(request([packet("user-1", "Change a setting")]), (event) => {
      events.push(event);
    });

    expect(tool).not.toHaveBeenCalled();
    const failure = events.find(
      (event) => event.type === "tool_end" && event.callId === "bad-configure",
    );
    expect(failure).toMatchObject({
      type: "tool_end",
      isError: true,
      result: {
        details: {
          errorCode: "invalid_arguments",
          field: "value",
          recoverable: true,
          toolName: "configure",
        },
      },
    });
    expect(JSON.stringify(failure)).not.toContain("private-invalid-rate");
  });

  it("routes conversation lifecycle actions with the exact turn identity", async () => {
    const faux = createFauxCore({ provider: "radcliffe-session-action-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "manage_session",
          { operation: "switch", reference: "Project notes" },
          { id: "session-switch-1" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("Please confirm the conversation switch."),
    ]);
    const tool = vi.fn(async () => ({
      modelContent: '{"operation":"switch","status":"awaiting_confirmation"}',
      details: { sessionTransitionStatus: "awaiting_confirmation" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet("user-1", "Switch to Project notes", {
          annotations: [
            ["projection", "context-v2"],
            ["radcliffe.turn_id", "turn-session-action"],
          ],
        }),
      ]),
      () => undefined,
    );

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        cancellationId: "cancellation-123456",
        turnId: "turn-session-action",
        toolName: "manage_session",
        arguments: { operation: "switch", reference: "Project notes" },
      }),
      expect.anything(),
      expect.any(Function),
    );
  });

  it("projects effective response detail into the model system prompt", () => {
    const context = modelContextFromRequest({
      ...request([packet("user-1", "Explain it")]),
      responseDetail: "detailed",
    });

    expect(context.systemPrompt).toContain("Give a detailed response");
    expect(context.tools.map((tool) => tool.name)).toContain("configure");
    expect(context.tools.map((tool) => tool.name)).toContain("manage_session");
    expect(context.tools.map((tool) => tool.name)).toContain("run_job");
    expect(context.tools.map((tool) => tool.name)).toContain("manage_job");
    expect(context.tools.map((tool) => tool.name)).toEqual(RADCLIFFE_TOOL_NAMES);
  });

  it("publishes provider-compatible object schemas with standard enums", () => {
    for (const schema of [
      CONFIGURE_PARAMETERS,
      MANAGE_SESSION_PARAMETERS,
      RUN_JOB_PARAMETERS,
      MANAGE_JOB_PARAMETERS,
      READ_JOB_SOURCE_PARAMETERS,
    ]) {
      const wireSchema = schema as unknown as Record<string, unknown>;
      expect(wireSchema.type).toBe("object");
      expect(wireSchema).not.toHaveProperty("anyOf");
      expect(wireSchema).toHaveProperty("properties");
      expect(wireSchema).toHaveProperty("required");
    }
    const manageSessionSchema = MANAGE_SESSION_PARAMETERS as unknown as {
      properties: Record<string, unknown>;
    };
    const manageJobSchema = MANAGE_JOB_PARAMETERS as unknown as {
      properties: Record<string, unknown>;
    };
    const runJobSchema = RUN_JOB_PARAMETERS as unknown as {
      properties: Record<string, unknown>;
    };
    expect(manageSessionSchema.properties.operation).toMatchObject({
      type: "string",
      enum: ["create", "switch", "restart", "end"],
    });
    expect(manageJobSchema.properties.operation).toMatchObject({
      type: "string",
      enum: ["status", "follow_up", "set_priority", "cancel", "retry"],
    });
    expect(runJobSchema.properties.notificationPolicy).toMatchObject({
      type: "string",
      enum: ["immediate", "next_safe_pause", "silent", "user_waiting"],
    });
    expect(runJobSchema.properties).not.toHaveProperty("maxRuntimeSeconds");
  });

  it("ignores the stale 30-second limit observed in the latest phone conversation", () => {
    expect(
      prepareRunJobArguments({
        instruction: "Get a numbered list of the top headlines on Hacker News.",
        notificationPolicy: "email",
        priority: 5,
        budgetUsd: 0,
        maxRuntimeSeconds: 30,
      }),
    ).toEqual({
      instruction: "Get a numbered list of the top headlines on Hacker News.",
    });
    expect(prepareManageSessionArguments({ action: "create" })).toEqual({
      operation: "create",
    });
    expect(prepareManageJobArguments({ action: "status", jobId: "job-1" })).toEqual({
      operation: "status",
      jobId: "job-1",
    });
    expect(() =>
      prepareManageJobArguments({ action: "cancel", jobId: "job-1" }),
    ).toThrow(/valid operation/);
    expect(
      prepareReadJobSourceArguments({
        reference: "headlines.md",
        jobId: "job-1",
        rate: "fast",
      }),
    ).toEqual({ reference: "headlines.md", jobId: "job-1", rate: "1.25x" });
  });

  it("keeps the foreground model from shortening a phone-requested job", async () => {
    const faux = createFauxCore({ provider: "radcliffe-weak-tool-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "run_job",
          {
            instruction: "Get a numbered list of the top headlines on Hacker News.",
            notificationPolicy: "email",
            priority: 5,
            budgetUsd: 0,
            maxRuntimeSeconds: 30,
          },
          { id: "weak-run-job" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("The background job was accepted."),
    ]);
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"queued"}',
      details: { jobId: "job-1" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet(
          "user-1",
          "Hey there, can you get a numbered list of the top headlines on Hacker News right now and then read it out loud to me",
        ),
      ]),
      () => undefined,
    );

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "run_job",
        userMessage:
          "Hey there, can you get a numbered list of the top headlines on Hacker News right now and then read it out loud to me",
        arguments: {
          instruction: "Get a numbered list of the top headlines on Hacker News.",
        },
      }),
      expect.anything(),
      expect.any(Function),
    );
  });

  it("accepts only explicit bounded same-session job input references", () => {
    expect(
      prepareRunJobArguments({
        instruction: "Extract the staged HTML without downloading it again.",
        inputs: [
          { kind: "source", reference: "radcliffe-demo.md" },
          {
            kind: "artifact",
            reference: "downloaded-page.html",
            jobId: "job-prior",
          },
        ],
      }),
    ).toEqual({
      instruction: "Extract the staged HTML without downloading it again.",
      inputs: [
        { kind: "source", reference: "radcliffe-demo.md" },
        {
          kind: "artifact",
          reference: "downloaded-page.html",
          jobId: "job-prior",
        },
      ],
    });
    expect(() =>
      prepareRunJobArguments({
        instruction: "Try a cross-boundary path.",
        inputs: [{ kind: "source", reference: "../secret.md" }],
      }),
    ).toThrow(/authorized source or artifact/);
    expect(() =>
      prepareRunJobArguments({
        instruction: "A source cannot come from a job ID.",
        inputs: [{ kind: "source", reference: "source.md", jobId: "job-other" }],
      }),
    ).toThrow(/authorized source or artifact/);
    expect(() =>
      prepareRunJobArguments({
        instruction: "Unknown input controls fail closed.",
        inputs: [{ kind: "artifact", reference: "page.html", hostPath: "/tmp/page.html" }],
      }),
    ).toThrow(/authorized source or artifact/);
  });

  it("accepts a background job without folding its private context into the response", async () => {
    const faux = createFauxCore({ provider: "radcliffe-background-job-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "run_job",
          {
            instruction: "Search six months of private lab papers",
            notificationPolicy: "next_safe_pause",
            priority: 1,
            budgetUsd: 0.5,
          },
          { id: "run-job-1" },
        ),
        { stopReason: "toolUse" },
      ),
      (context) => {
        const result = context.messages.find(
          (message) => message.role === "toolResult" && message.toolCallId === "run-job-1",
        );
        expect(result?.content).toEqual([
          {
            type: "text",
            text:
              "The background job was accepted. " +
              "Acknowledge this job in one brief sentence with no additional clauses or information. " +
              "Do not quote job identifiers or serialized job metadata.",
          },
        ]);
        return fauxAssistantMessage("Working on it.");
      },
    ]);
    const tool = vi.fn(async () => ({
      modelContent:
        '{"jobId":"job-1","notificationPolicy":"next_safe_pause","status":"queued"}',
      details: { jobId: "job-1", replayed: "false" },
    }));
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(request([packet("user-1", "Research this while we talk")]), (event) => {
      events.push(event);
    });

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "run_job",
        userMessage: "Research this while we talk",
        arguments: {
          instruction: "Search six months of private lab papers",
          notificationPolicy: "next_safe_pause",
          priority: 1,
          budgetUsd: 0.5,
        },
      }),
      expect.anything(),
      expect.any(Function),
    );
    expect(
      events
        .filter((event) => event.type === "model_delta")
        .map((event) => ("text" in event ? event.text : ""))
        .join(""),
    ).toBe("Working on it.");
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "Working on it.",
    });
    expect(
      JSON.stringify(
        events.filter((event) => event.type === "model_delta" || event.type === "model_complete"),
      ),
    ).not.toContain("job-1");
  });

  it("sends one exact current-turn user message without prior user turns", async () => {
    const faux = createFauxCore({ provider: "radcliffe-current-turn-job-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "run_job",
          { instruction: "Download the exact source selected by the user." },
          { id: "current-turn-run-job" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("I started it."),
    ]);
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"queued"}',
      details: { jobId: "job-1" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet("old-user", "An unrelated older request.", {
          annotations: [["radcliffe.turn_id", "turn-old"]],
        }),
        packet("fragment-one", "Use the Bancroft version exactly.", {
          annotations: [["radcliffe.turn_id", "turn-current"]],
        }),
        packet("fragment-two", "Run that as a background job.", {
          annotations: [["radcliffe.turn_id", "turn-current"]],
        }),
      ]),
      () => undefined,
    );

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "run_job",
        userMessage: "Use the Bancroft version exactly.\nRun that as a background job.",
      }),
      expect.anything(),
      expect.any(Function),
    );
    expect(JSON.stringify(tool.mock.calls)).not.toContain("unrelated older request");
  });

  it("keeps a failed background-job acceptance brief and truthful", async () => {
    const faux = createFauxCore({ provider: "radcliffe-background-job-error-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("run_job", { instruction: "Run a failing job" }, { id: "run-job-error" }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("The request failed with verbose private gateway details."),
    ]);
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(
      faux.getModel(),
      faux.streamSimple,
      vi.fn(async () => {
        throw new Error("private gateway failure");
      }),
    );

    await orchestrator.run(request([packet("user-1", "Start that job")]), (event) => {
      events.push(event);
    });

    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I couldn't start that job.",
    });
    expect(
      events.filter((event) => event.type === "model_delta" || event.type === "model_complete"),
    ).not.toContainEqual(expect.objectContaining({ text: expect.stringContaining("gateway") }));
  });

  it("routes an idempotent background job cancellation by exact job ID", async () => {
    const faux = createFauxCore({ provider: "radcliffe-manage-job-test" });
    faux.setResponses([
      (context) => {
        expect(JSON.stringify(context.messages)).toContain(
          "Internal background job reference: job-1",
        );
        return fauxAssistantMessage(
          fauxToolCall(
            "manage_job",
            { operation: "cancel", jobId: "job-1" },
            { id: "cancel-1" },
          ),
          { stopReason: "toolUse" },
        );
      },
      fauxAssistantMessage("Cancellation was requested."),
    ]);
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"running","cancelRequested":true}',
      details: { jobId: "job-1", replayed: "false" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet(
          "job-reference-1",
          "Internal background job reference: job-1. Never quote the ID aloud.",
          { role: "tool", source: "call" },
        ),
        packet("user-1", "Cancel that job"),
      ]),
      () => {},
    );

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "manage_job",
        arguments: { operation: "cancel", jobId: "job-1" },
      }),
      expect.anything(),
      expect.any(Function),
    );
  });

  it("fails closed when the model claims cancellation without manage_job", async () => {
    const faux = createFauxCore({ provider: "radcliffe-ungrounded-job-test" });
    faux.setResponses([
      fauxAssistantMessage(
        "Your job has been cancelled. Job ID: job-private-machine-identifier.",
      ),
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn();
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([
        packet(
          "user-1",
          "Okay, that's great. Now go ahead and cancel that job. This is a test.",
        ),
      ]),
      (event) => {
        events.push(event);
      },
    );

    expect(tool).not.toHaveBeenCalled();
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I couldn't cancel that job.",
    });
    expect(JSON.stringify(events)).not.toContain("job-private-machine-identifier");
  });

  it("forces and grounds a cancellation while preserving useful context after a brief action", async () => {
    const faux = createFauxCore({ provider: "radcliffe-grounded-job-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("manage_job", { operation: "cancel", jobId: "job-1" }, { id: "cancel-1" }),
        { stopReason: "toolUse" },
      ),
      (context) => {
        const result = context.messages.find(
          (message) => message.role === "toolResult" && message.toolCallId === "cancel-1",
        );
        expect(JSON.stringify(result?.content)).not.toContain("job-1");
        expect(JSON.stringify(result?.content)).toContain("one short sentence");
        return fauxAssistantMessage(
          "I requested cancellation. I’ll keep the comparison available while it stops.",
        );
      },
    ]);
    const events: SidecarEvent[] = [];
    const stream = vi.fn(faux.streamSimple);
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"running","cancelRequested":true}',
      details: { jobId: "job-1", status: "running", replayed: "false" },
    }));
    const model = { ...faux.getModel(), api: "openai-completions" as const };
    const orchestrator = new PiAgentOrchestrator(model, stream, tool);

    await orchestrator.run(
      request([packet("user-1", "Please cancel that background job now.")]),
      (event) => {
        events.push(event);
      },
    );

    expect(stream.mock.calls[0]?.[2]).toMatchObject({
      toolChoice: { type: "function", function: { name: "manage_job" } },
    });
    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "manage_job",
        arguments: { operation: "cancel", jobId: "job-1" },
      }),
      expect.anything(),
      expect.any(Function),
    );
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I requested cancellation. I’ll keep the comparison available while it stops.",
    });
    expect(
      JSON.stringify(
        events.filter((event) => event.type === "model_delta" || event.type === "model_complete"),
      ),
    ).not.toContain("job-1");
  });

  it("does not ground cancellation on a mismatched successful status call", async () => {
    const faux = createFauxCore({ provider: "radcliffe-mismatched-job-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("manage_job", { operation: "status", jobId: "job-1" }, { id: "status-1" }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("The job is cancelled."),
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"running"}',
      details: { jobId: "job-1", status: "running" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([packet("user-1", "Cancel that job.")]),
      (event) => {
        events.push(event);
      },
    );

    expect(tool).toHaveBeenCalledOnce();
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I couldn't cancel that job.",
    });
  });

  it.each([
    {
      requestText: "Add this as a follow-up to the job.",
      operation: "follow_up" as const,
      arguments: { operation: "follow_up" as const, jobId: "job-1", instruction: "Use CSV." },
      response: "I added it. The final comparison will use CSV.",
    },
    {
      requestText: "Make the background job high priority.",
      operation: "set_priority" as const,
      arguments: { operation: "set_priority" as const, jobId: "job-1", priority: 4 },
      response: "I reprioritized it. The original analysis scope is unchanged.",
    },
    {
      requestText: "Can you just retry it?",
      operation: "retry" as const,
      arguments: { operation: "retry" as const, jobId: "job-1" },
      response: "I retried it. The original request is running again.",
    },
  ])("grounds an explicit $operation response without swallowing useful context", async (scenario) => {
    const faux = createFauxCore({ provider: `radcliffe-${scenario.operation}-test` });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("manage_job", scenario.arguments, { id: `${scenario.operation}-1` }),
        { stopReason: "toolUse" },
      ),
      (context) => {
        const result = context.messages.find(
          (message) =>
            message.role === "toolResult" && message.toolCallId === `${scenario.operation}-1`,
        );
        expect(JSON.stringify(result?.content)).not.toContain("job-1");
        expect(JSON.stringify(result?.content)).toContain("additional useful context");
        return fauxAssistantMessage(scenario.response);
      },
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn(async () => ({
      modelContent: '{"jobId":"job-1","status":"running"}',
      details: { jobId: "job-1", status: "running" },
    }));
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(request([packet("user-1", scenario.requestText)]), (event) => {
      events.push(event);
    });

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "manage_job",
        userMessage: scenario.requestText,
        arguments: scenario.arguments,
      }),
      expect.anything(),
      expect.any(Function),
    );
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: scenario.response,
    });
    expect(
      JSON.stringify(
        events.filter((event) => event.type === "model_delta" || event.type === "model_complete"),
      ),
    ).not.toContain("job-1");
  });

  it("fails a status claim closed when manage_job errors", async () => {
    const faux = createFauxCore({ provider: "radcliffe-status-error-test" });
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall("manage_job", { operation: "status", jobId: "job-1" }, { id: "status-1" }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("The job is complete."),
    ]);
    const events: SidecarEvent[] = [];
    const tool = vi.fn(async () => {
      throw new Error("private gateway failure");
    });
    const model = { ...faux.getModel(), api: "anthropic-messages" as const };
    const stream = vi.fn(faux.streamSimple);
    const orchestrator = new PiAgentOrchestrator(model, stream, tool);

    await orchestrator.run(request([packet("user-1", "Report the progress on that job.")]), (event) => {
      events.push(event);
    });

    expect(stream.mock.calls[0]?.[2]).toMatchObject({
      toolChoice: { type: "tool", name: "manage_job" },
    });
    expect(events).toContainEqual({
      type: "model_complete",
      requestId: "request-1",
      text: "I couldn't check that job.",
    });
    expect(
      events.filter((event) => event.type === "model_delta" || event.type === "model_complete"),
    ).not.toContainEqual(expect.objectContaining({ text: "private gateway failure" }));
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

  it("resolves a named promoted job source through direct canonical rendering", async () => {
    const faux = createFauxCore({ provider: "radcliffe-job-source-test" });
    const sourceEnvelope = "opaque-promoted-source-envelope";
    faux.setResponses([
      fauxAssistantMessage(
        fauxToolCall(
          "read_job_source",
          { reference: "Scaling Laws", jobId: "job-1", rate: "fast" },
          { id: "read-job-source-1" },
        ),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage("The promoted source is being read."),
    ]);
    const tool = vi.fn(async () => ({
      modelContent: "source:promoted-paper",
      details: {
        sourceMessageId: "source:read-job-source-1",
        sourceEnvelopeProtoBase64: sourceEnvelope,
      },
    }));
    const events: SidecarEvent[] = [];
    const orchestrator = new PiAgentOrchestrator(faux.getModel(), faux.streamSimple, tool);

    await orchestrator.run(
      request([packet("new-user", "Read the Scaling Laws abstract exactly")]),
      (event) => {
        events.push(event);
      },
    );

    expect(tool).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "read_job_source",
        arguments: { reference: "Scaling Laws", jobId: "job-1", rate: "1.25x" },
      }),
      expect.anything(),
      expect.any(Function),
    );
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "tool_end",
        toolName: "read_job_source",
        isError: false,
      }),
    );
  });

  it("maps tool gateway statuses to content-safe stable failure codes", () => {
    expect(sanitizeToolFailure("read_job_source", new Error("Tool Gateway returned HTTP 404")))
      .toMatchObject({
        details: {
          errorCode: "not_found",
          recoverable: false,
          toolName: "read_job_source",
        },
      });
    expect(sanitizeToolFailure("manage_job", new Error("Tool Gateway returned HTTP 409")))
      .toMatchObject({ details: { errorCode: "conflict" } });
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

  it("tells the model where a heard source stopped so it can resume exactly", () => {
    const heard = (boundaryIndex: string, complete: string) =>
      convertRadcliffeContext([
        {
          role: "radcliffe",
          packet: packet("heard", "Obvious enough.", {
            role: "source",
            source: "text_source",
            annotations: [
              ["locator", "nicotine.md"],
              ["radcliffe.playback.boundary_index", boundaryIndex],
              ["radcliffe.playback.complete", complete],
            ],
          }),
        },
      ])[0]?.content;

    // A finished sentence resumes at the next one.
    expect(heard("9", "true")).toContain(
      "[Radcliffe text_source from nicotine.md, sentence 9, resume at startAtBoundary 10]",
    );
    // A sentence cut off mid-playback is replayed from its own start.
    expect(heard("9", "false")).toContain(
      "[Radcliffe text_source from nicotine.md, part of sentence 9, resume at startAtBoundary 9]",
    );
  });

  it("leaves non-source packets and unmarked sources without a reading position", () => {
    const converted = convertRadcliffeContext([
      {
        role: "radcliffe",
        packet: packet("plain", "Canonical", {
          role: "source",
          source: "text_source",
          annotations: [["locator", "demo.md"]],
        }),
      },
      { role: "radcliffe", packet: packet("user", "Do you know why?") },
    ]);

    expect(converted.map((message) => message.content)).toEqual([
      "[Radcliffe text_source from demo.md]\nCanonical",
      "[Radcliffe user]\nDo you know why?",
    ]);
  });

  it("preserves harness job speech and outcome ahead of the next user message", () => {
    const converted = convertRadcliffeContext([
      {
        role: "radcliffe",
        packet: packet("announcement", "A background job failed.", {
          role: "system",
          source: "system",
        }),
      },
      {
        role: "radcliffe",
        packet: packet(
          "outcome",
          "Internal background job outcome for job-1: failed. The job agent exhausted its allowed steps.",
          { role: "tool", source: "call" },
        ),
      },
      { role: "radcliffe", packet: packet("user", "Do you know why?") },
    ]);

    expect(converted.map((message) => message.content)).toEqual([
      "[Radcliffe system]\nA background job failed.",
      "[Radcliffe call]\nInternal background job outcome for job-1: failed. The job agent exhausted its allowed steps.",
      "[Radcliffe user]\nDo you know why?",
    ]);
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
