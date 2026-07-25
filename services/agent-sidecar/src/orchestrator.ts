import {
  Agent,
  type AgentMessage,
  type AgentTool,
  type StreamFn,
} from "@earendil-works/pi-agent-core";
import {
  type AssistantMessage,
  type Message,
  type Model,
  Type,
} from "@earendil-works/pi-ai";

import type { ContextMeter } from "./context-meter.js";
import type {
  ContextPacket,
  ContextUsage,
  SidecarEvent,
  SpeechRate,
  ToolExecutionRequest,
  ToolExecutor,
  TurnRequest,
} from "./protocol.js";
import { splitCompletedSpeechSegments, spokenOutputFor } from "./spoken-output.js";

interface RadcliffeContextMessage {
  role: "radcliffe";
  packet: ContextPacket;
}

declare module "@earendil-works/pi-agent-core" {
  interface CustomAgentMessages {
    radcliffe: RadcliffeContextMessage;
  }
}

export interface AgentOrchestrator {
  run(
    request: TurnRequest,
    emit: (event: SidecarEvent) => void | Promise<void>,
    signal?: AbortSignal,
  ): Promise<void>;
}

export const RADCLIFFE_TOOL_NAMES = [
  "list_sources",
  "read_source",
  "read_job_source",
  "configure",
  "manage_session",
  "run_job",
  "manage_job",
] as const;

type ConfigureArguments = Extract<
  ToolExecutionRequest,
  { toolName: "configure" }
>["arguments"];
type ReadSourceArguments = Extract<
  ToolExecutionRequest,
  { toolName: "read_source" }
>["arguments"];
type ReadJobSourceArguments = Extract<
  ToolExecutionRequest,
  { toolName: "read_job_source" }
>["arguments"];
type ManageSessionArguments = Extract<
  ToolExecutionRequest,
  { toolName: "manage_session" }
>["arguments"];
type RunJobArguments = Extract<ToolExecutionRequest, { toolName: "run_job" }>["arguments"];
type ManageJobArguments = Extract<
  ToolExecutionRequest,
  { toolName: "manage_job" }
>["arguments"];
export type JobManagementOperation = ManageJobArguments["operation"];

const CONFIGURATION_OPERATIONS = ["inspect", "set", "reset", "undo"] as const;
const CONFIGURATION_KEYS = ["response_detail", "default_speech_rate"] as const;
const CONFIGURATION_SCOPES = ["turn", "session"] as const;
const RESPONSE_DETAILS = ["brief", "standard", "detailed"] as const;
const SPEECH_RATES = ["0.75x", "1.0x", "1.25x", "1.5x"] as const;
const SESSION_OPERATIONS = ["create", "switch", "restart", "end"] as const;
const JOB_NOTIFICATION_POLICIES = [
  "immediate",
  "next_safe_pause",
  "silent",
  "user_waiting",
] as const;
const JOB_OPERATIONS = ["status", "follow_up", "set_priority", "cancel", "retry"] as const;
const JOB_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$/;
const TEXTUAL_TOOL_PROTOCOL =
  /<\/?(?:function_calls?|invoke|parameter|tool_calls?|tool_call)\b|```[^\n]*\n\s*(?:configure|list_sources|manage_job|manage_session|read_job_source|read_source|run_job)\b|(?:^|\n)\s*(?:configure|list_sources|manage_job|manage_session|read_job_source|read_source|run_job)\s*(?:\(|\n\s*(?:\{|[A-Za-z_][\w-]*\s*:))|"(?:name|tool|tool_name)"\s*:\s*"(?:configure|list_sources|manage_job|manage_session|read_job_source|read_source|run_job)"/i;

export interface DeliveryIntent {
  rate: SpeechRate;
  scope: "turn" | "session";
  sourceRead: boolean;
}

interface DeliveryPreparation {
  intent: DeliveryIntent;
  applied: boolean;
}

export type ExactOutputKind =
  | "numbers"
  | "identifier"
  | "hash"
  | "url"
  | "json"
  | "path"
  | "timestamp"
  | "literal"
  | "characters";

export interface ExactOutputIntent {
  kind: ExactOutputKind;
  literal?: string;
  sequence?: {
    start: number;
    end: number;
  };
}

export interface ExactOutputValidation {
  valid: boolean;
  reasons: string[];
}

const EXACT_SEQUENCE_ITEM_LIMIT = 200;
const ABBREVIATED_OUTPUT = /(?:\.{3}|…|\band so (?:on|forth)\b|\betc\.?\b|\bthrough\s+\d+\b)/i;

const SPEECH_RATE_ALIASES = new Map<string, SpeechRate>([
  ["0.75", "0.75x"],
  ["0.75x", "0.75x"],
  ["slow", "0.75x"],
  ["slowly", "0.75x"],
  ["slower", "0.75x"],
  ["1", "1.0x"],
  ["1.0", "1.0x"],
  ["1x", "1.0x"],
  ["1.0x", "1.0x"],
  ["normal", "1.0x"],
  ["normally", "1.0x"],
  ["regular", "1.0x"],
  ["default", "1.0x"],
  ["1.25", "1.25x"],
  ["1.25x", "1.25x"],
  ["fast", "1.25x"],
  ["faster", "1.25x"],
  ["quick", "1.25x"],
  ["quickly", "1.25x"],
  ["1.5", "1.5x"],
  ["1.5x", "1.5x"],
  ["very fast", "1.5x"],
  ["very_fast", "1.5x"],
  ["fastest", "1.5x"],
]);

function invalidToolArgument(toolName: string, field: string): Error {
  return new Error(`tool_argument_invalid:${toolName}:${field}`);
}

export function normalizeSpeechRate(value: unknown): SpeechRate | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return SPEECH_RATE_ALIASES.get(String(value));
  }
  if (typeof value !== "string") return undefined;
  return SPEECH_RATE_ALIASES.get(value.trim().toLowerCase().replace(/\s+/g, " "));
}

export function classifyDeliveryIntent(text: string): DeliveryIntent | undefined {
  const normalized = text.trim().toLowerCase().replace(/\s+/g, " ");
  const rateMatch = normalized.match(
    /\b(0\.75x|1(?:\.0)?x|1\.25x|1\.5x|very[_ ]fast|fastest|faster|fast|quickly|quick|slower|slowly|slow|normally|normal|regular|default)\b/,
  );
  const explicitNumericRate = normalized.match(
    /\b(?:at|rate(?: of)?|speed(?: of)?)\s+(0\.75|1(?:\.0)?|1\.25|1\.5)\b/,
  );
  const rate = normalizeSpeechRate(rateMatch?.[1] ?? explicitNumericRate?.[1]);
  if (!rate) return undefined;
  if (
    !/\b(?:speak|say|talk|answer|respond|reply|count|read|recite|deliver|pace|rate)\b/.test(
      normalized,
    ) &&
    !/\b(?:slow down|speed up)\b/.test(normalized)
  ) {
    return undefined;
  }
  const sourceRead = hasExplicitSourceReadIntent(normalized);
  const persistent =
    /\b(?:for (?:this|the) session|session default|default (?:speech )?(?:pace|rate)|from now on|going forward|until i change|keep (?:speaking|talking)|always)\b/.test(
      normalized,
    );
  return { rate, scope: persistent ? "session" : "turn", sourceRead };
}

function quotedLiteral(text: string): string | undefined {
  const doubleQuoted = text.match(/["“]([^"”]{1,4000})["”]/s)?.[1];
  if (doubleQuoted) return doubleQuoted;
  const singleQuoted = text.match(/(?:^|\s)'([^']{1,4000})'(?:\s|[.!?]|$)/s)?.[1];
  return singleQuoted;
}

function trimCapturedLiteral(value: string): string {
  return value.trim().replace(/[.!?]+$/, "");
}

function numericSequenceFromPacket(packet: ContextPacket): number[] {
  const sequenceLine = packet.content.split(/\r?\n/, 1)[0] ?? "";
  return [...sequenceLine.matchAll(/\b\d+\b/g)].map((match) => Number(match[0]));
}

function continuationIntent(history: readonly ContextPacket[]): ExactOutputIntent | undefined {
  const lastCountIndex = history.findLastIndex(
    (packet) =>
      packet.source === "user" &&
      /\bcount\b/i.test(packet.content) &&
      /\b(?:from\s+\d+\s+to|to)\s+\d+\b/i.test(packet.content),
  );
  if (lastCountIndex < 0) return undefined;
  const original = classifyExactOutputIntent(history[lastCountIndex]!.content);
  if (!original?.sequence) return undefined;
  const durable = history
    .slice(lastCountIndex + 1)
    .filter((packet) => packet.source === "model" && packet.role === "assistant")
    .at(-1);
  if (!durable) return undefined;
  const emitted = numericSequenceFromPacket(durable);
  const last = emitted.at(-1);
  if (last === undefined) return undefined;
  return {
    kind: "numbers",
    sequence: { start: last + 1, end: original.sequence.end },
  };
}

export function classifyExactOutputIntent(
  text: string,
  history: readonly ContextPacket[] = [],
): ExactOutputIntent | undefined {
  const normalized = text.trim();
  if (/\b(?:keep going|continue|resume)\b/i.test(normalized)) {
    return continuationIntent(history);
  }
  const range = normalized.match(/\bcount(?:\s+\w+){0,3}\s+from\s+(\d+)\s+to\s+(\d+)\b/i);
  if (range) {
    const start = Number(range[1]);
    const end = Number(range[2]);
    if (Number.isSafeInteger(start) && Number.isSafeInteger(end) && start >= 0 && end >= start) {
      return { kind: "numbers", sequence: { start, end } };
    }
  }
  const countTo = normalized.match(/\bcount(?:\s+\w+){0,3}\s+to\s+(\d+)\b/i);
  if (countTo) {
    const end = Number(countTo[1]);
    if (Number.isSafeInteger(end) && end >= 1) {
      return { kind: "numbers", sequence: { start: 1, end } };
    }
  }
  const exactLanguage =
    /\b(?:exactly|verbatim|character[- ]by[- ]character|spell out|exact reading|read exactly|repeat exactly)\b/i.test(
      normalized,
    );
  if (!exactLanguage) return undefined;
  const characters = /\b(?:character[- ]by[- ]character|spell out)\b/i.test(normalized);
  const quoted = quotedLiteral(normalized);
  if (characters && quoted) return { kind: "characters", literal: quoted };
  if (/\b(?:id|identifier)\b/i.test(normalized) && quoted) {
    return { kind: "identifier", literal: quoted };
  }
  const hash = normalized.match(/\b[a-fA-F0-9]{16,128}\b/)?.[0];
  if (/\bhash\b/i.test(normalized) && hash) return { kind: "hash", literal: hash };
  const url = normalized.match(/https?:\/\/[^\s"'<>]+/i)?.[0]?.replace(/[),.!?]+$/, "");
  if (url) return { kind: "url", literal: url };
  const timestamp = normalized.match(
    /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b/,
  )?.[0];
  if (timestamp) return { kind: "timestamp", literal: timestamp };
  if (/\bjson\b/i.test(normalized)) {
    const fenced = normalized.match(/```(?:json)?\s*([\s\S]*?)\s*```/i)?.[1];
    const object = normalized.match(/(\{[\s\S]*\}|\[[\s\S]*\])/s)?.[1];
    const literal = fenced ?? object ?? quoted;
    if (literal) return { kind: "json", literal: literal.trim() };
  }
  const path = normalized.match(
    /(?:^|\s)((?:\/[A-Za-z0-9._-]+){2,}|(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+)(?=\s|[.!?]|$)/,
  )?.[1];
  if (/\bpath\b/i.test(normalized) && path) {
    return { kind: "path", literal: path };
  }
  const identifier = normalized.match(
    /\b(?:id|identifier)\b[^\n:]*:\s*([A-Za-z0-9][A-Za-z0-9_.:-]{2,})/i,
  )?.[1];
  if (identifier) return { kind: "identifier", literal: trimCapturedLiteral(identifier) };
  if (quoted) return { kind: "literal", literal: quoted };
  const afterColon = normalized.match(/:\s*([^\n]{1,4000})$/s)?.[1];
  if (afterColon) return { kind: "literal", literal: trimCapturedLiteral(afterColon) };
  return undefined;
}

function expectedNumericItems(intent: ExactOutputIntent): number[] {
  if (!intent.sequence) return [];
  const last = Math.min(
    intent.sequence.end,
    intent.sequence.start + EXACT_SEQUENCE_ITEM_LIMIT - 1,
  );
  return Array.from({ length: Math.max(0, last - intent.sequence.start + 1) }, (_, index) =>
    intent.sequence!.start + index,
  );
}

function renderCharacters(value: string): string {
  return [...value].join(", ");
}

export function validateExactOutput(
  intent: ExactOutputIntent,
  candidate: string,
): ExactOutputValidation {
  const reasons: string[] = [];
  if (ABBREVIATED_OUTPUT.test(candidate)) reasons.push("abbreviated");
  if (intent.kind === "numbers") {
    const observed = [...candidate.matchAll(/\b\d+\b/g)].map((match) => Number(match[0]));
    const expected = expectedNumericItems(intent);
    if (observed.length < expected.length) reasons.push("missing");
    if (observed.length > expected.length) reasons.push("duplicated_or_extra");
    if (
      observed.length === expected.length &&
      observed.some((value, index) => value !== expected[index])
    ) {
      reasons.push("reordered_or_replaced");
    }
  } else if (intent.literal !== undefined) {
    const expected =
      intent.kind === "characters" ? renderCharacters(intent.literal) : intent.literal;
    if (candidate.trim() !== expected) reasons.push("literal_mismatch");
  } else if (!candidate.trim()) {
    reasons.push("empty");
  }
  return { valid: reasons.length === 0, reasons };
}

function renderNumericSequence(intent: ExactOutputIntent): string {
  if (!intent.sequence) return "I could not determine the requested numeric range.";
  if (intent.sequence.start > intent.sequence.end) {
    return `The durable sequence already reached ${intent.sequence.end}.`;
  }
  const items = expectedNumericItems(intent);
  const groups: string[] = [];
  for (let index = 0; index < items.length; index += 10) {
    groups.push(`${items.slice(index, index + 10).join(", ")}.`);
  }
  const reached = items.at(-1);
  if (reached !== undefined && reached < intent.sequence.end) {
    groups.push(
      "The sequence above stops at the last displayed number because this turn reached the " +
        "exact-output limit. Say keep going to continue from durable output.",
    );
  }
  return groups.join(" ");
}

export function finalizeExactOutput(intent: ExactOutputIntent, candidate: string): string {
  const validation = validateExactOutput(intent, candidate);
  if (validation.valid) return candidate.trim();
  if (intent.kind === "numbers") return renderNumericSequence(intent);
  if (intent.literal !== undefined) {
    return intent.kind === "characters" ? renderCharacters(intent.literal) : intent.literal;
  }
  return "I could not complete that exact-output request without risking an inexact result.";
}

function stringEnum(values: readonly string[]) {
  return Type.String({ enum: [...values] });
}

function argumentRecord(value: unknown, toolName: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw invalidToolArgument(toolName, "arguments");
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => key in value) && Object.keys(value).every((key) => allowed.has(key));
}

function normalizeOperationAlias(
  value: Record<string, unknown>,
  safeOperations: readonly string[],
): Record<string, unknown> {
  if (
    value.operation !== undefined ||
    typeof value.action !== "string" ||
    !safeOperations.includes(value.action)
  ) {
    return value;
  }
  const normalized: Record<string, unknown> = { ...value, operation: value.action };
  delete normalized.action;
  return normalized;
}

function isMember<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && values.includes(value as T);
}

export function prepareConfigureArguments(value: unknown): ConfigureArguments {
  const item = {
    ...normalizeOperationAlias(argumentRecord(value, "configure"), ["inspect"]),
  };
  if (item.key === "default_speech_rate" && item.operation === "set") {
    item.value = normalizeSpeechRate(item.value);
  }
  if (
    !exactKeys(item, ["operation", "key"], ["scope", "value", "undoMutationId"]) ||
    !isMember(CONFIGURATION_OPERATIONS, item.operation) ||
    !isMember(CONFIGURATION_KEYS, item.key) ||
    (item.scope !== undefined && !isMember(CONFIGURATION_SCOPES, item.scope))
  ) {
    throw invalidToolArgument("configure", "operation_or_key");
  }
  if (item.operation === "set") {
    const validValue =
      item.key === "response_detail"
        ? isMember(RESPONSE_DETAILS, item.value)
        : isMember(SPEECH_RATES, item.value);
    if (!validValue || item.undoMutationId !== undefined) {
      throw invalidToolArgument("configure", "value");
    }
  } else if (item.value !== undefined) {
    throw invalidToolArgument("configure", "value");
  }
  if (
    item.operation === "undo"
      ? typeof item.undoMutationId !== "string" ||
        item.undoMutationId.length < 1 ||
        item.undoMutationId.length > 128
      : item.undoMutationId !== undefined
  ) {
    throw invalidToolArgument("configure", "undoMutationId");
  }
  return item as ConfigureArguments;
}

function prepareSourceRateArguments(
  value: unknown,
  toolName: "read_source" | "read_job_source",
  identityField: "path" | "reference",
): Record<string, unknown> {
  const item = { ...argumentRecord(value, toolName) };
  if (!exactKeys(item, [identityField], ["rate"])) {
    throw invalidToolArgument(toolName, "arguments");
  }
  if (
    typeof item[identityField] !== "string" ||
    item[identityField].length < 1 ||
    item[identityField].length > (identityField === "path" ? 4096 : 300)
  ) {
    throw invalidToolArgument(toolName, identityField);
  }
  if (item.rate !== undefined) {
    const normalized = normalizeSpeechRate(item.rate);
    if (!normalized) throw invalidToolArgument(toolName, "rate");
    item.rate = normalized;
  }
  return item;
}

export function prepareReadSourceArguments(value: unknown): ReadSourceArguments {
  return prepareSourceRateArguments(value, "read_source", "path") as ReadSourceArguments;
}

export function prepareReadJobSourceArguments(value: unknown): ReadJobSourceArguments {
  const item = { ...argumentRecord(value, "read_job_source") };
  if (!exactKeys(item, ["reference"], ["jobId", "rate"])) {
    throw invalidToolArgument("read_job_source", "arguments");
  }
  const prepared = prepareSourceRateArguments(
    { reference: item.reference, ...(item.rate === undefined ? {} : { rate: item.rate }) },
    "read_job_source",
    "reference",
  );
  if (item.jobId !== undefined) {
    if (typeof item.jobId !== "string" || !JOB_ID.test(item.jobId)) {
      throw invalidToolArgument("read_job_source", "jobId");
    }
    prepared.jobId = item.jobId;
  }
  return prepared as ReadJobSourceArguments;
}

function toolResultText(value: unknown): string | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const content = (value as { content?: unknown }).content;
  if (!Array.isArray(content)) return undefined;
  for (const block of content) {
    if (
      typeof block === "object" &&
      block !== null &&
      (block as { type?: unknown }).type === "text" &&
      typeof (block as { text?: unknown }).text === "string"
    ) {
      return (block as { text: string }).text;
    }
  }
  return undefined;
}

export function sanitizeToolFailure(toolName: string, value: unknown): unknown {
  const message = value instanceof Error ? value.message : toolResultText(value);
  const match = message?.match(/^tool_argument_invalid:([a-z_]+):([a-z_]+)$/);
  if (match?.[1] === toolName) {
    return {
      content: [
        {
          type: "text",
          text: "The tool arguments were invalid. Correct the named field and retry.",
        },
      ],
      details: {
        errorCode: "invalid_arguments",
        field: match[2],
        recoverable: true,
        toolName,
      },
    };
  }
  const status = message?.match(/^Tool Gateway returned HTTP (\d{3})$/)?.[1];
  const errorCode =
    status === "403"
      ? "authorization_denied"
      : status === "404"
        ? "not_found"
        : status === "409"
          ? "conflict"
          : status === "422"
            ? "invalid_request"
            : status === "503"
              ? "gateway_unavailable"
              : "execution_failed";
  return {
    content: [{ type: "text", text: "The tool could not be completed." }],
    details: {
      errorCode,
      recoverable: false,
      toolName,
    },
  };
}

export function prepareManageSessionArguments(value: unknown): ManageSessionArguments {
  const item = normalizeOperationAlias(argumentRecord(value, "manage_session"), ["create"]);
  if (
    !exactKeys(item, ["operation"], ["reference"]) ||
    !isMember(SESSION_OPERATIONS, item.operation)
  ) {
    throw new Error("manage_session requires a valid operation");
  }
  if (
    item.operation === "switch"
      ? typeof item.reference !== "string" ||
        item.reference.length < 1 ||
        item.reference.length > 120
      : item.reference !== undefined
  ) {
    throw new Error("only manage_session switch requires a recent-conversation reference");
  }
  return item as ManageSessionArguments;
}

export function prepareRunJobArguments(value: unknown): RunJobArguments {
  const item = { ...argumentRecord(value, "run_job") };
  // Older prompts allowed the foreground model to choose this policy value. Ignore a
  // stale model-supplied value so it cannot shorten or extend the harness-owned deadline.
  delete item.maxRuntimeSeconds;
  if (
    !exactKeys(
      item,
      ["instruction"],
      ["notificationPolicy", "priority", "budgetUsd"],
    ) ||
    typeof item.instruction !== "string" ||
    item.instruction.length < 1 ||
    item.instruction.length > 12_000
  ) {
    throw new Error("run_job requires one bounded instruction and no unknown controls");
  }
  if (!isMember(JOB_NOTIFICATION_POLICIES, item.notificationPolicy)) {
    delete item.notificationPolicy;
  }
  if (!Number.isInteger(item.priority) || Number(item.priority) < 0 || Number(item.priority) > 4) {
    delete item.priority;
  }
  if (
    typeof item.budgetUsd !== "number" ||
    !Number.isFinite(item.budgetUsd) ||
    item.budgetUsd <= 0 ||
    item.budgetUsd > 100
  ) {
    delete item.budgetUsd;
  }
  return item as RunJobArguments;
}

export function prepareManageJobArguments(value: unknown): ManageJobArguments {
  const item = normalizeOperationAlias(argumentRecord(value, "manage_job"), ["status"]);
  if (
    !exactKeys(item, ["operation", "jobId"], ["instruction", "priority"]) ||
    !isMember(JOB_OPERATIONS, item.operation) ||
    typeof item.jobId !== "string" ||
    !JOB_ID.test(item.jobId)
  ) {
    throw new Error("manage_job requires a valid operation and exact job ID");
  }
  if (
    item.operation === "follow_up"
      ? typeof item.instruction !== "string" ||
        item.instruction.length < 1 ||
        item.instruction.length > 4_000
      : item.instruction !== undefined
  ) {
    throw new Error("only manage_job follow_up requires an instruction");
  }
  if (
    item.operation === "set_priority"
      ? !Number.isInteger(item.priority) || Number(item.priority) < 0 || Number(item.priority) > 4
      : item.priority !== undefined
  ) {
    throw new Error("only manage_job set_priority requires a priority from 0 through 4");
  }
  return item as ManageJobArguments;
}

const JOB_REFERENCE = String.raw`(?:background\s+)?(?:job|task|work|agent)`;

export function explicitJobManagementOperation(text: string): JobManagementOperation | undefined {
  const normalized = text.trim().toLowerCase().replace(/\s+/g, " ");
  if (!normalized) return undefined;
  const asksHowTo =
    /\b(?:how (?:can|could|do|would) i|can i|could i|tell me how|explain how)\b/.test(normalized);
  if (
    !asksHowTo &&
    (/\b(?:retry|rerun)(?:\s+(?:(?:the|that)\s+)?(?:job|task|work|it))?\b/.test(normalized) ||
      /\btry\s+(?:(?:the|that)\s+)?(?:job|task|work|it)\s+again\b/.test(normalized))
  ) {
    return "retry";
  }
  if (
    !asksHowTo &&
    (new RegExp(`\\b(?:cancel|abort|stop|terminate)\\b.{0,48}\\b(?:${JOB_REFERENCE})\\b`).test(
      normalized,
    ) ||
      new RegExp(`\\b(?:${JOB_REFERENCE})\\b.{0,48}\\b(?:cancel|abort|stop|terminate)\\b`).test(
        normalized,
      ))
  ) {
    return "cancel";
  }
  if (
    !asksHowTo &&
    /\b(?:set|change|make|raise|lower)\b.{0,48}\bpriorit(?:y|ize)\b/.test(normalized)
  ) {
    return "set_priority";
  }
  if (
    !asksHowTo &&
    (/\b(?:add|send|give)\b.{0,48}\bfollow[- ]?up\b/.test(normalized) ||
      /\btell\b.{0,16}\b(?:job|agent)\b.{0,16}\bto\b/.test(normalized))
  ) {
    return "follow_up";
  }
  if (
    /\b(?:report|check|show|give me|what(?:'s| is))\b.{0,48}\b(?:status|progress)\b/.test(
      normalized,
    ) ||
    /\b(?:status|progress)\b.{0,32}\b(?:job|task|work)\b/.test(normalized)
  ) {
    return "status";
  }
  return undefined;
}

export function explicitJobLaunchIntent(text: string): boolean {
  const normalized = text.trim().toLowerCase().replace(/\s+/g, " ");
  if (!normalized || /\b(?:how (?:can|could|do|would) i|tell me how|explain how)\b/.test(normalized)) {
    return false;
  }
  return (
    /\b(?:start|launch|create|run|kick off|spin up)\b.{0,48}\b(?:background|batchground)\s+(?:job|task|work)\b/.test(
      normalized,
    ) ||
    /\b(?:start|launch|create|run|kick off|spin up)\b.{0,24}\b(?:a\s+)?(?:new\s+)?(?:job|task)\b/.test(
      normalized,
    ) ||
    /\b(?:background|batchground)\s+(?:job|task)\b.{0,48}\b(?:start|launch|create|run)\b/.test(
      normalized,
    )
  );
}

export function containsTextualToolProtocol(text: string): boolean {
  return TEXTUAL_TOOL_PROTOCOL.test(text);
}

function requiredToolStream(base: StreamFn, toolName: "manage_job" | "run_job"): StreamFn {
  let firstRequest = true;
  return (model, context, options) => {
    if (!firstRequest) return base(model, context, options);
    firstRequest = false;
    const toolChoice =
      model.api === "anthropic-messages"
        ? { type: "tool", name: toolName }
        : model.api === "openai-completions"
          ? { type: "function", function: { name: toolName } }
          : undefined;
    if (!toolChoice) return base(model, context, options);
    return base(model, context, { ...options, toolChoice } as Parameters<StreamFn>[2]);
  };
}

function toolDisabledStream(base: StreamFn): StreamFn {
  return (model, context, options) =>
    base(model, context, {
      ...options,
      toolChoice: "none",
    } as Parameters<StreamFn>[2]);
}

function toolResultDetails(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const details = (value as Record<string, unknown>).details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) return undefined;
  return details as Record<string, unknown>;
}

function groundedJobResponse(
  operation: JobManagementOperation,
  succeeded: boolean,
  modelResponse: string,
  details?: Record<string, unknown>,
): string {
  if (!succeeded) {
    return operation === "status"
      ? "I couldn't check that job."
      : operation === "cancel"
        ? "I couldn't cancel that job."
        : operation === "retry"
          ? "I couldn't retry that job."
        : "I couldn't update that job.";
  }
  if (modelResponse.trim()) return modelResponse;
  if (operation === "cancel") return "I requested cancellation.";
  if (operation === "follow_up") return "I added that to the job.";
  if (operation === "set_priority") return "I changed the job priority.";
  if (operation === "retry") return "I retried the job.";
  const status = typeof details?.status === "string" ? details.status : "available";
  return `The job status is ${status}.`;
}

const JOB_ACTION_RESPONSE_INSTRUCTION =
  "Describe this job tool action in one short sentence using only a few words. " +
  "Then provide any additional useful context the user requested; do not omit it. " +
  "Do not quote job identifiers or serialized job metadata.";

function runJobModelContent(): string {
  return `The background job was accepted. ${JOB_ACTION_RESPONSE_INSTRUCTION}`;
}

function manageJobModelContent(
  parameters: ManageJobArguments,
  result: { modelContent: string },
): string {
  if (parameters.operation === "status") return result.modelContent;
  const outcome =
    parameters.operation === "cancel"
      ? "The cancellation request was accepted."
      : parameters.operation === "follow_up"
        ? "The job follow-up was accepted."
        : parameters.operation === "retry"
          ? "The failed job was queued for a fresh bounded attempt."
        : "The priority change was accepted.";
  return `${outcome} ${JOB_ACTION_RESPONSE_INSTRUCTION}`;
}

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

export const RADCLIFFE_SYSTEM_PROMPT =
  "The voice-agent harness is called Radcliffe. You are the selected conversational model " +
  "operating inside it. You may inspect, set, reset, or undo only response_detail and " +
  "default_speech_rate with configure. " +
  "If the user ever asks for something that you can't do, run a background job to do it. " +
  "After starting a background job, only mention it briefly in 5 words or less, like 'working on it'. " +
  "Similarly, when a job is done, say so very succinctly, like 'I got that article about hexachromatic vision', " +
  "or 'Found a few takes on the Category Theory question.' " +
  "Treat included Radcliffe system and call packets as durable chronological conversation " +
  "records; when one reports a background-job outcome before the current user message, use it " +
  "to ground your answer and call manage_job if more detail is needed. " +
  "Map concise or short to brief, normal to standard, " +
  "thorough or verbose to detailed, slowly to 0.75x, regular to 1.0x, quickly to 1.25x, and " +
  "very fast to 1.5x. Use turn scope for a temporary request and session scope when the user " +
  "asks the change to continue. You may create a new " +
  "conversation, switch to a named recent conversation, restart into a fresh conversation, or " +
  "end the current conversation with manage_session. Initiate research, analysis, download, or coding " +
  "work with run_job; it returns an accepted job ID promptly while this foreground conversation " +
  "continues. Follow the schema exactly for every tool call, including required field names and " +
  "value types. Tool invocations must use the provider's structured tool channel. Never print XML, JSON, " +
  "function tags, invoke tags, parameter tags, or a textual imitation of a tool call. " +
  "Call job tools without a preamble. " +
  "Use manage_job to inspect, follow up, reprioritize, cancel, or retry a failed " +
  "existing job. You can discover authorized " +
  "Markdown filenames with list_sources and read one with read_source. " +
  // "When a completed job status returns findings, cite their promoted source titles or locators and " +
  // "explicitly label every finding whose inference flag is true. Never detach a finding from its " +
  // "source entity IDs. " +
  "If the user identifies a source informally or the exact authorized path " +
  "is uncertain, call list_sources first without narrating the lookup. Use only a returned path; " +
  "if multiple paths are genuinely plausible, ask the user which one. " +
  "Use read_job_source when the user asks to read a named source promoted by a completed " +
  "background job. Pass its human title, artifact Markdown filename, or entity ID and include the " +
  "exact hidden job ID when the chronological job outcome identifies it; an ambiguous title must be clarified, never " +
  "guessed. " +
  "Any time the user asks you to read something, use the `read_source` tool. " +
  // "An explicit exact-output request overrides response-detail brevity. Never abbreviate an exact " +
  // "enumeration, identifier, hash, URL, JSON value, path, timestamp, quotation, verbatim reading, " +
  // "or character sequence with an ellipsis, a summary, or 'and so on'. " +
  // "Optimize ordinary responses for listening. Do not volunteer or laboriously read hashes, UUIDs, " +
  // "internal IDs, raw URLs, filesystem paths, machine timestamps, stack traces, serialized data, " +
  // "long numbers, code, citation machinery, repeated punctuation, or acronym-heavy metadata. State " +
  // "the human meaning instead. If machine detail is useful in the visual conversation, include it " +
  // "once with a nearby plain-language description; Radcliffe will provide a speakable alternative. " +
  // "Only read or spell machine detail when the user explicitly requests exact delivery. " +
  "No Emojis. Do not use emoji characters in responses. ";
export const LIST_SOURCES_DESCRIPTION =
  "List the bounded catalog of relative Markdown paths authorized for this session. " +
  "Returns filenames only, never file contents or absolute host paths.";
export const LIST_SOURCES_PARAMETERS = Type.Object({}, { additionalProperties: false });
export const READ_SOURCE_DESCRIPTION =
  "Read a canonical UTF-8 Markdown source using a Radcliffe capability grant. " +
  "Normalize spoken filename punctuation before passing the path. An explicitly requested speech " +
  "rate must be one of 0.75x, 1.0x, 1.25x, or 1.5x.";
export const READ_SOURCE_PARAMETERS = Type.Unsafe<ReadSourceArguments>(
  Type.Object(
    {
      path: Type.String({ minLength: 1, maxLength: 4096 }),
      rate: Type.Optional(stringEnum(SPEECH_RATES)),
    },
    { additionalProperties: false },
  ),
);
export const READ_JOB_SOURCE_DESCRIPTION =
  "Read one canonical Markdown source promoted or safely derived from a completed background job. " +
  "Resolve it by human title, Markdown artifact filename, or exact entity ID within this " +
  "conversation. Include the hidden exact job ID when known; ambiguous references fail closed.";
export const READ_JOB_SOURCE_PARAMETERS = Type.Unsafe<ReadJobSourceArguments>(
  Type.Object(
    {
      reference: Type.String({ minLength: 1, maxLength: 300 }),
      jobId: Type.Optional(
        Type.String({ minLength: 1, maxLength: 128, pattern: JOB_ID.source }),
      ),
      rate: Type.Optional(stringEnum(SPEECH_RATES)),
    },
    { additionalProperties: false },
  ),
);
export const CONFIGURE_DESCRIPTION =
  "Inspect, set, reset, or undo one registered Radcliffe setting. Only response_detail and " +
  "default_speech_rate are allowed. Turn overrides expire after the current turn; session values " +
  "persist for this conversation.";
export const CONFIGURE_PARAMETERS = Type.Unsafe<ConfigureArguments>(
  Type.Object(
    {
      operation: stringEnum(CONFIGURATION_OPERATIONS),
      key: stringEnum(CONFIGURATION_KEYS),
      scope: Type.Optional(stringEnum(CONFIGURATION_SCOPES)),
      value: Type.Optional(stringEnum([...RESPONSE_DETAILS, ...SPEECH_RATES])),
      undoMutationId: Type.Optional(Type.String({ minLength: 1, maxLength: 128 })),
    },
    { additionalProperties: false },
  ),
);
export const MANAGE_SESSION_DESCRIPTION =
  "Create a fresh conversation, resolve and switch to an authorized recent conversation, " +
  "restart into a fresh conversation, or end the current conversation. Switch, restart, and " +
  "end require user confirmation. Never guess when choices are returned.";
export const MANAGE_SESSION_PARAMETERS = Type.Unsafe<ManageSessionArguments>(
  Type.Object(
    {
      operation: stringEnum(SESSION_OPERATIONS),
      reference: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
    },
    { additionalProperties: false },
  ),
);
export const RUN_JOB_DESCRIPTION =
  "Accept a bounded background research, analysis, download, or coding job without blocking the " +
  "foreground conversation. Returns a durable job ID immediately; completion arrives separately.";
export const RUN_JOB_PARAMETERS = Type.Unsafe<RunJobArguments>(
  Type.Object(
    {
      instruction: Type.String({ minLength: 1, maxLength: 12000 }),
      notificationPolicy: Type.Optional(stringEnum(JOB_NOTIFICATION_POLICIES)),
      priority: Type.Optional(Type.Integer({ minimum: 0, maximum: 4 })),
      budgetUsd: Type.Optional(Type.Number({ exclusiveMinimum: 0, maximum: 100 })),
    },
    { additionalProperties: false },
  ),
);
export const MANAGE_JOB_DESCRIPTION =
  "Inspect, add a private follow-up to, reprioritize, cancel, or retry a failed durable " +
  "background job by its exact job ID. Job output remains separate from the foreground " +
  "conversation.";
export const MANAGE_JOB_PARAMETERS = Type.Unsafe<ManageJobArguments>(
  Type.Object(
    {
      operation: stringEnum(JOB_OPERATIONS),
      jobId: Type.String({ minLength: 1, maxLength: 96, pattern: JOB_ID.source }),
      instruction: Type.Optional(Type.String({ minLength: 1, maxLength: 4000 })),
      priority: Type.Optional(Type.Integer({ minimum: 0, maximum: 4 })),
    },
    { additionalProperties: false },
  ),
);

function isRadcliffeMessage(message: AgentMessage): message is RadcliffeContextMessage {
  return "role" in message && message.role === "radcliffe";
}

function label(packet: ContextPacket): string {
  const annotations = Object.fromEntries(packet.annotations);
  const provenance = annotations.locator ? ` from ${annotations.locator}` : "";
  return `[Radcliffe ${packet.source}${provenance}]\n${packet.content}`;
}

export function convertRadcliffeContext(messages: AgentMessage[]): Message[] {
  return messages.flatMap((message): Message[] => {
    if (!isRadcliffeMessage(message)) {
      if (message.role === "user" || message.role === "assistant" || message.role === "toolResult") {
        return [message];
      }
      return [];
    }
    const packet = message.packet;
    if (!packet.include) return [];
    if (packet.role === "assistant") {
      const assistant: AssistantMessage = {
        role: "assistant",
        content: [{ type: "text", text: packet.content }],
        api: "radcliffe-context",
        provider: "radcliffe",
        model: packet.projectionVersion,
        usage: EMPTY_USAGE,
        stopReason: "stop",
        timestamp: packet.timestamp,
      };
      return [assistant];
    }
    return [{ role: "user", content: label(packet), timestamp: packet.timestamp }];
  });
}

function exactOutputInstruction(intent: ExactOutputIntent): string {
  if (intent.kind === "numbers" && intent.sequence) {
    return (
      ` Fidelity constraint: emit every integer from ${intent.sequence.start} through ` +
      `${intent.sequence.end} exactly once and in order. This overrides response-detail brevity; ` +
      "do not use words in place of digits, ellipses, ranges, summaries, or fabricated progress."
    );
  }
  return (
    ` Fidelity constraint: this is an exact ${intent.kind} request. Return only the requested ` +
    "value without a prefix, suffix, explanation, normalization, omission, or abbreviation."
  );
}

function systemPrompt(
  request: TurnRequest,
  delivery?: DeliveryPreparation,
  exactOutput?: ExactOutputIntent,
): string {
  return (
    RADCLIFFE_SYSTEM_PROMPT +
    (request.responseDetail === "brief"
      ? " Keep this response brief."
      : request.responseDetail === "detailed"
        ? " Give a detailed response."
        : " Use standard response detail.") +
    (delivery
      ? delivery.applied
        ? ` The requested ${delivery.intent.rate} speech rate is already applied for this ${delivery.intent.scope}. Answer directly and do not call configure or a source-reading tool for it.`
        : " The requested speech rate could not be applied. Answer directly without claiming the rate changed."
      : "") +
    (exactOutput ? exactOutputInstruction(exactOutput) : "")
  );
}

function hasExplicitSourceReadIntent(text: string): boolean {
  const normalized = text.toLowerCase();
  return (
    /\b(?:read|recite)\b/.test(normalized) &&
    (/\b(?:source|file|document|markdown|path|abstract|paper|article|report|chapter)\b/.test(
      normalized,
    ) ||
      /(?:\.md\b|\bdot m d\b)/.test(normalized))
  );
}

function exactOutputFromPackets(packets: readonly ContextPacket[]): ExactOutputIntent | undefined {
  const current = packets.at(-1);
  if (!current || current.source !== "user" || hasExplicitSourceReadIntent(current.content)) {
    return undefined;
  }
  return classifyExactOutputIntent(current.content, packets.slice(0, -1));
}

function includedPacketsForRequest(request: TurnRequest): ContextPacket[] {
  const packets = request.packets.filter((packet) => packet.include);
  const current = packets.at(-1);
  if (!request.userMessage || current?.source !== "user") return packets;
  return [...packets.slice(0, -1), { ...current, content: request.userMessage }];
}

export function modelContextFromRequest(request: TurnRequest) {
  const packets = includedPacketsForRequest(request);
  const messages: RadcliffeContextMessage[] = packets.map((packet) => ({
    role: "radcliffe",
    packet,
  }));
  return {
    systemPrompt: systemPrompt(request, undefined, exactOutputFromPackets(packets)),
    messages: convertRadcliffeContext(messages),
    tools: [
      {
        name: "list_sources",
        description: LIST_SOURCES_DESCRIPTION,
        parameters: LIST_SOURCES_PARAMETERS,
      },
      {
        name: "read_source",
        description: READ_SOURCE_DESCRIPTION,
        parameters: READ_SOURCE_PARAMETERS,
      },
      {
        name: "read_job_source",
        description: READ_JOB_SOURCE_DESCRIPTION,
        parameters: READ_JOB_SOURCE_PARAMETERS,
      },
      {
        name: "configure",
        description: CONFIGURE_DESCRIPTION,
        parameters: CONFIGURE_PARAMETERS,
      },
      {
        name: "manage_session",
        description: MANAGE_SESSION_DESCRIPTION,
        parameters: MANAGE_SESSION_PARAMETERS,
      },
      {
        name: "run_job",
        description: RUN_JOB_DESCRIPTION,
        parameters: RUN_JOB_PARAMETERS,
      },
      {
        name: "manage_job",
        description: MANAGE_JOB_DESCRIPTION,
        parameters: MANAGE_JOB_PARAMETERS,
      },
    ],
  };
}

export class PiAgentOrchestrator implements AgentOrchestrator {
  constructor(
    private readonly model: Model<string>,
    private readonly streamFn: StreamFn,
    private readonly executeTool: ToolExecutor,
    private readonly contextMeter?: ContextMeter,
    private readonly streamFnForTurn?: (request: TurnRequest) => StreamFn,
  ) {}

  async measure(request: TurnRequest): Promise<ContextUsage> {
    if (!this.contextMeter) throw new Error("context accounting is unavailable");
    return this.contextMeter.measure(request);
  }

  async run(
    request: TurnRequest,
    emit: (event: SidecarEvent) => void | Promise<void>,
    signal?: AbortSignal,
  ): Promise<void> {
    if (this.contextMeter) {
      const usage = await this.contextMeter.measure(request);
      if (usage.overflow) {
        throw new Error(
          `context overflow: ${usage.projectedInputTokens} input + ` +
            `${usage.reservedOutputTokens} reserved > ${usage.contextLimit} token limit`,
        );
      }
    }
    const packets = includedPacketsForRequest(request);
    if (packets.length === 0) throw new Error("turn requires an included context packet");
    const current = packets.at(-1)!;
    const deliveryIntent =
      current.source === "user" ? classifyDeliveryIntent(current.content) : undefined;
    const exactOutputIntent = exactOutputFromPackets(packets);
    const jobOperation =
      current.source === "user" ? explicitJobManagementOperation(current.content) : undefined;
    const jobLaunch = current.source === "user" && explicitJobLaunchIntent(current.content);
    const turnId =
      Object.fromEntries(current.annotations)["radcliffe.turn_id"] ?? current.messageId;
    const userMessage =
      request.userMessage ??
      packets
        .filter(
          (packet) =>
            packet.source === "user" &&
            (Object.fromEntries(packet.annotations)["radcliffe.turn_id"] ?? packet.messageId) ===
              turnId,
        )
        .map((packet) => packet.content)
        .join("\n");
    let deliveryPreparation: DeliveryPreparation | undefined;
    if (deliveryIntent && !deliveryIntent.sourceRead) {
      const callId = `delivery-rate:${request.requestId}`;
      const parameters: ConfigureArguments = {
        operation: "set",
        key: "default_speech_rate",
        scope: deliveryIntent.scope,
        value: deliveryIntent.rate,
      };
      await emit({
        type: "tool_start",
        requestId: request.requestId,
        callId,
        toolName: "configure",
        arguments: parameters,
      });
      try {
        const result = await this.executeTool(
          {
            sessionId: request.sessionId,
            generation: request.generation,
            operationId: request.operationId!,
            cancellationId: request.cancellationId!,
            traceId: request.traceId!,
            turnId,
            callId,
            idempotencyKey: `${request.requestId}:${callId}`,
            toolName: "configure",
            arguments: parameters,
          },
          signal,
        );
        deliveryPreparation = { intent: deliveryIntent, applied: true };
        await emit({
          type: "tool_end",
          requestId: request.requestId,
          callId,
          toolName: "configure",
          isError: false,
          result: {
            content: [{ type: "text", text: result.modelContent }],
            details: result.details,
          },
        });
      } catch {
        deliveryPreparation = { intent: deliveryIntent, applied: false };
        await emit({
          type: "tool_end",
          requestId: request.requestId,
          callId,
          toolName: "configure",
          isError: true,
          result: {
            content: [{ type: "text", text: "The speech rate could not be applied." }],
            details: {
              errorCode: "delivery_configuration_failed",
              recoverable: false,
              toolName: "configure",
            },
          },
        });
      }
    }
    let modelText = "";
    let pendingDisplayText = "";
    let groundedJobCall = false;
    let groundedJobDetails: Record<string, unknown> | undefined;
    const matchingJobCalls = new Set<string>();
    let providerError: string | undefined;
    let textualToolProtocolViolation = false;
    let textualToolProtocolRecoveries = 0;
    const listSources: AgentTool<typeof LIST_SOURCES_PARAMETERS> = {
      name: "list_sources",
      label: "List sources",
      description: LIST_SOURCES_DESCRIPTION,
      parameters: LIST_SOURCES_PARAMETERS,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        _params: object,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "list_sources",
          arguments: {},
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const readSource: AgentTool<typeof READ_SOURCE_PARAMETERS> = {
      name: "read_source",
      label: "Read source",
      description: READ_SOURCE_DESCRIPTION,
      parameters: READ_SOURCE_PARAMETERS,
      prepareArguments: prepareReadSourceArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ReadSourceArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "read_source",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const readJobSource: AgentTool<typeof READ_JOB_SOURCE_PARAMETERS> = {
      name: "read_job_source",
      label: "Read background job source",
      description: READ_JOB_SOURCE_DESCRIPTION,
      parameters: READ_JOB_SOURCE_PARAMETERS,
      prepareArguments: prepareReadJobSourceArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ReadJobSourceArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "read_job_source",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const configure: AgentTool<typeof CONFIGURE_PARAMETERS> = {
      name: "configure",
      label: "Configure Radcliffe",
      description: CONFIGURE_DESCRIPTION,
      parameters: CONFIGURE_PARAMETERS,
      prepareArguments: prepareConfigureArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ConfigureArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "configure",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const manageSession: AgentTool<typeof MANAGE_SESSION_PARAMETERS> = {
      name: "manage_session",
      label: "Manage conversation",
      description: MANAGE_SESSION_DESCRIPTION,
      parameters: MANAGE_SESSION_PARAMETERS,
      prepareArguments: prepareManageSessionArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ManageSessionArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "manage_session",
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: result.modelContent }],
          details: result.details,
        };
      },
    };
    const runJob: AgentTool<typeof RUN_JOB_PARAMETERS> = {
      name: "run_job",
      label: "Run background job",
      description: RUN_JOB_DESCRIPTION,
      parameters: RUN_JOB_PARAMETERS,
      prepareArguments: prepareRunJobArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: RunJobArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "run_job",
          userMessage,
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: runJobModelContent() }],
          details: result.details,
        };
      },
    };
    const manageJob: AgentTool<typeof MANAGE_JOB_PARAMETERS> = {
      name: "manage_job",
      label: "Manage background job",
      description: MANAGE_JOB_DESCRIPTION,
      parameters: MANAGE_JOB_PARAMETERS,
      prepareArguments: prepareManageJobArguments,
      executionMode: "sequential" as const,
      execute: async (
        toolCallId: string,
        params: ManageJobArguments,
        toolSignal?: AbortSignal,
        onUpdate?: (value: {
          content: Array<{ type: "text"; text: string }>;
          details: unknown;
        }) => void,
      ) => {
        const toolRequest: ToolExecutionRequest = {
          sessionId: request.sessionId,
          generation: request.generation,
          operationId: request.operationId!,
          cancellationId: request.cancellationId!,
          traceId: request.traceId!,
          turnId,
          callId: toolCallId,
          idempotencyKey: `${request.requestId}:${toolCallId}`,
          toolName: "manage_job",
          userMessage,
          arguments: params,
        };
        const result = await this.executeTool(toolRequest, toolSignal, (update) => {
          onUpdate?.({
            content: [{ type: "text", text: JSON.stringify(update) }],
            details: update,
          });
        });
        return {
          content: [{ type: "text" as const, text: manageJobModelContent(params, result) }],
          details: result.details,
        };
      },
    };
    const history: RadcliffeContextMessage[] = packets.slice(0, -1).map((packet) => ({
      role: "radcliffe",
      packet,
    }));
    const turnStream = this.streamFnForTurn?.(request) ?? this.streamFn;
    const allTools = [
      listSources,
      readSource,
      readJobSource,
      configure,
      manageSession,
      runJob,
      manageJob,
    ];
    const tools = exactOutputIntent
      ? allTools
      : deliveryIntent
        ? deliveryIntent.sourceRead
          ? [listSources, readSource, readJobSource, manageSession, runJob, manageJob]
          : [manageSession, runJob, manageJob]
        : allTools;
    const controlledStream = exactOutputIntent
      ? toolDisabledStream(turnStream)
      : jobOperation
        ? requiredToolStream(turnStream, "manage_job")
        : jobLaunch
          ? requiredToolStream(turnStream, "run_job")
          : turnStream;
    const agent = new Agent({
      initialState: {
        systemPrompt: systemPrompt(request, deliveryPreparation, exactOutputIntent),
        model: this.model,
        thinkingLevel: "off",
        tools,
        messages: history,
      },
      convertToLlm: convertRadcliffeContext,
      transformContext: async (messages) => messages,
      streamFn: controlledStream,
      sessionId: request.sessionId,
      toolExecution: "sequential",
    });
    let runJobCallSeen = false;
    let runJobSucceeded = false;
    const emitSpeechSegments = async (flush: boolean) => {
      const split = splitCompletedSpeechSegments(pendingDisplayText, flush);
      pendingDisplayText = split.remainder;
      for (const segment of split.segments) {
        const spoken = spokenOutputFor(segment);
        await emit({
          type: "model_delta",
          requestId: request.requestId,
          text: segment,
          ...(spoken.text !== segment
            ? {
                spokenText: spoken.text,
                spokenCategories: spoken.categories,
              }
            : {}),
        });
      }
    };
    const unsubscribe = agent.subscribe(async (event) => {
      if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
        const candidate = modelText + event.assistantMessageEvent.delta;
        if (!exactOutputIntent && containsTextualToolProtocol(candidate)) {
          textualToolProtocolViolation = true;
          modelText = "";
          pendingDisplayText = "";
          return;
        }
        if (textualToolProtocolViolation) return;
        if (
          (!runJobCallSeen || runJobSucceeded) &&
          (!jobOperation || groundedJobCall)
        ) {
          modelText += event.assistantMessageEvent.delta;
          if (!exactOutputIntent) {
            pendingDisplayText += event.assistantMessageEvent.delta;
            await emitSpeechSegments(false);
          }
        }
      } else if (event.type === "message_end" && event.message.role === "assistant") {
        const complete = event.message.content
          .filter((block) => block.type === "text")
          .map((block) => block.text)
          .join("");
        const protocolViolation =
          !exactOutputIntent &&
          (textualToolProtocolViolation || containsTextualToolProtocol(complete));
        if (protocolViolation) {
          const hasNativeToolCall = event.message.content.some((block) => block.type === "toolCall");
          modelText = "";
          pendingDisplayText = "";
          textualToolProtocolViolation = false;
          if (!hasNativeToolCall && textualToolProtocolRecoveries < 1) {
            textualToolProtocolRecoveries += 1;
            agent.followUp({
              role: "user",
              content: [
                {
                  type: "text",
                  text:
                    "Your previous response used textual tool syntax, so it was discarded. " +
                    "Retry now using only the provider's structured tool channel. Follow the " +
                    "schema exactly. Do not " +
                    "describe, print, or imitate the tool call.",
                },
              ],
              timestamp: Date.now(),
            });
          } else if (!hasNativeToolCall) {
            providerError = "model repeatedly emitted textual tool protocol";
          }
        }
        if (
          !protocolViolation &&
          complete &&
          (!runJobCallSeen || runJobSucceeded) &&
          (!jobOperation || groundedJobCall)
        ) {
          modelText = complete;
        }
        if (event.message.stopReason === "error") {
          providerError = event.message.errorMessage ?? "model provider failed";
        }
        if (!protocolViolation && !exactOutputIntent && pendingDisplayText) {
          await emitSpeechSegments(true);
        }
        await emit({
          type: "usage",
          requestId: request.requestId,
          provider: event.message.provider,
          modelId: event.message.responseModel ?? event.message.model,
          inputTokens: event.message.usage.input,
          outputTokens: event.message.usage.output,
          cacheReadTokens: event.message.usage.cacheRead,
          cacheWriteTokens: event.message.usage.cacheWrite,
          estimatedCostUsd: event.message.usage.cost.total,
        });
      } else if (event.type === "tool_execution_start") {
        if (event.toolName === "run_job") {
          runJobCallSeen = true;
        }
        if (
          jobOperation &&
          event.toolName === "manage_job" &&
          typeof event.args === "object" &&
          event.args !== null &&
          (event.args as Record<string, unknown>).operation === jobOperation
        ) {
          matchingJobCalls.add(event.toolCallId);
        }
        await emit({
          type: "tool_start",
          requestId: request.requestId,
          callId: event.toolCallId,
          toolName: event.toolName,
          arguments: event.args,
        });
      } else if (event.type === "tool_execution_update") {
        await emit({
          type: "tool_update",
          requestId: request.requestId,
          callId: event.toolCallId,
          toolName: event.toolName,
          update: event.partialResult,
        });
      } else if (event.type === "tool_execution_end") {
        if (event.toolName === "run_job" && !event.isError) {
          runJobSucceeded = true;
        }
        if (matchingJobCalls.has(event.toolCallId) && !event.isError) {
          groundedJobCall = true;
          groundedJobDetails = toolResultDetails(event.result);
        }
        await emit({
          type: "tool_end",
          requestId: request.requestId,
          callId: event.toolCallId,
          toolName: event.toolName,
          isError: event.isError,
          result: event.isError
            ? sanitizeToolFailure(event.toolName, event.result)
            : event.result,
        });
      }
    });
    const abort = () => agent.abort();
    signal?.addEventListener("abort", abort, { once: true });
    try {
      await agent.prompt({ role: "radcliffe", packet: current });
      if (providerError) throw new Error(providerError);
      if (runJobCallSeen) {
        if (!runJobSucceeded || !modelText.trim()) {
          modelText = runJobSucceeded ? "I started it." : "I couldn't start that job.";
          await emit({ type: "model_delta", requestId: request.requestId, text: modelText });
        }
      } else if (jobOperation) {
        const modelSuppliedResponse = Boolean(modelText.trim());
        modelText = groundedJobResponse(
          jobOperation,
          groundedJobCall,
          modelText,
          groundedJobDetails,
        );
        if (!groundedJobCall || !modelSuppliedResponse) {
          await emit({ type: "model_delta", requestId: request.requestId, text: modelText });
        }
      }
      if (exactOutputIntent) {
        modelText = finalizeExactOutput(exactOutputIntent, modelText);
        if (modelText) {
          await emit({ type: "model_delta", requestId: request.requestId, text: modelText });
        }
      }
      if (modelText) {
        await emit({ type: "model_complete", requestId: request.requestId, text: modelText });
      }
      await emit({ type: "complete", requestId: request.requestId });
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown Pi failure";
      await emit({
        type: "error",
        requestId: request.requestId,
        message,
        ...(message.startsWith("cloud session budget exhausted")
          ? { code: "cloud_budget_exhausted" as const }
          : {}),
      });
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      unsubscribe();
    }
  }
}
