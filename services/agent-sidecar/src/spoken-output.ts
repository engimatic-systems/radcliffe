export type SpeechHostileCategory =
  | "hash"
  | "identifier"
  | "url"
  | "path"
  | "timestamp"
  | "stack_trace"
  | "serialized_data"
  | "long_number"
  | "code"
  | "citation"
  | "repeated_punctuation"
  | "acronym_metadata";

export interface SpokenOutput {
  text: string;
  categories: SpeechHostileCategory[];
}

function friendlyHost(raw: string): string {
  try {
    return new URL(raw).hostname.replace(/^www\./, "").replaceAll(".", " dot ");
  } catch {
    return "the referenced site";
  }
}

function friendlyTimestamp(raw: string): string {
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "the displayed timestamp";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(parsed);
}

export function spokenOutputFor(displayText: string, preserveExact = false): SpokenOutput {
  if (preserveExact) return { text: displayText, categories: [] };
  const categories = new Set<SpeechHostileCategory>();
  let text = displayText;
  const replace = (
    category: SpeechHostileCategory,
    pattern: RegExp,
    replacement: string | ((substring: string) => string),
  ) => {
    text = text.replace(pattern, (substring) => {
      categories.add(category);
      return typeof replacement === "string" ? replacement : replacement(substring);
    });
  };

  replace("code", /```[\s\S]{1,8000}?```/g, "a code sample");
  replace("stack_trace", /(?:^|\n)\s*at\s+[^\n]{3,500}(?=\n|$)/g, " Technical details are available in the conversation.");
  replace(
    "serialized_data",
    /\{[^{}\n]{1,2000}:[^{}\n]{1,2000}\}|\[[^\[\]\n]{1,2000}(?:,|:")[^\[\]\n]{1,2000}\]/g,
    "structured data available in the conversation",
  );
  replace("url", /https?:\/\/[^\s"'<>]+/gi, (value) => `a link on ${friendlyHost(value)}`);
  replace(
    "timestamp",
    /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b/g,
    friendlyTimestamp,
  );
  replace(
    "path",
    /(?:[A-Za-z]:\\(?:[^\s\\]+\\)+[^\s\\]+|(?:\/[A-Za-z0-9._-]+){2,}|(?:[A-Za-z0-9._-]+\/){2,}[A-Za-z0-9._-]+)/g,
    "a file path shown in the conversation",
  );
  replace(
    "identifier",
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi,
    "an identifier shown in the conversation",
  );
  replace(
    "identifier",
    /\b(?:job|msg|message|session|ses|trace|trc|request|operation|playback|turn)[_:-][A-Za-z0-9._:-]{6,}\b/gi,
    "an internal identifier shown in the conversation",
  );
  replace("hash", /\b(?:sha(?:256|512)[:=-]?)?[a-f0-9]{16,128}\b/gi, "a hash shown in the conversation");
  replace("long_number", /\b\d{10,}\b/g, "a long number shown in the conversation");
  replace("code", /`[^`\n]{2,500}`/g, "inline code shown in the conversation");
  replace("citation", /\[(?:\d+[a-z]?(?:\s*[,;-]\s*)?){1,8}\]/gi, "a citation");
  replace(
    "acronym_metadata",
    /\b(?:[A-Z][A-Z0-9_]{1,11}\b[\s,;:/-]+){3,}[A-Z][A-Z0-9_]{1,11}\b/g,
    "technical metadata shown in the conversation",
  );
  replace("repeated_punctuation", /([!?.,:;*_#=-])\1{2,}/g, (value) => value[0]!);

  return {
    text: text.replace(/\s{2,}/g, " ").trimStart(),
    categories: [...categories].sort(),
  };
}

export function splitCompletedSpeechSegments(
  buffer: string,
  flush: boolean,
): { segments: string[]; remainder: string } {
  if (flush) return { segments: buffer ? [buffer] : [], remainder: "" };
  if (buffer.includes("```")) return { segments: [], remainder: buffer };
  const segments: string[] = [];
  let boundary = 0;
  const sentence = /.*?(?:[.!?](?:["')\]]*)\s+|\n+)/gs;
  for (const match of buffer.matchAll(sentence)) {
    segments.push(match[0]);
    boundary = (match.index ?? 0) + match[0].length;
  }
  return { segments, remainder: buffer.slice(boundary) };
}
