import {
  canonical,
  compareText,
  decide,
  digest,
  integer,
  requireValue as check,
  viewAssessment,
} from "./decision.js";

export const MAX_BYTES = 5 * 1024 * 1024;
const utf8 = new TextEncoder();
const tools = ["search", "read", "verify"];

// JSON.parse discards duplicate keys and the integer/float distinction.
export function parseBundle(raw) {
  check(
    typeof raw === "string" && utf8.encode(raw).length <= MAX_BYTES,
    "The file exceeds 5 MiB.",
  );
  let i = 0;
  const space = () => {
    while (/[\x20\r\n\t]/.test(raw[i] ?? "!")) i++;
  };
  const stringToken =
    /"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/y;
  const primitives = /(?:true|false|null|-?(?:0|[1-9]\d*))/y;
  function take(char) {
    space();
    if (raw[i] !== char) return false;
    i++;
    return true;
  }
  function readString() {
    stringToken.lastIndex = i;
    const token = stringToken.exec(raw);
    check(token, "Invalid JSON string.");
    i = stringToken.lastIndex;
    const result = JSON.parse(token[0]);
    canonical(result);
    return result;
  }
  function read(depth) {
    check(depth < 80, "JSON nesting exceeds the trace format.");
    space();
    if (raw[i] === '"') return readString();
    if (take("{")) {
      const obj = Object.create(null);
      if (take("}")) return obj;
      do {
        space();
        const key = readString();
        check(
          !Object.hasOwn(obj, key) && take(":"),
          "Duplicate key or malformed JSON object.",
        );
        obj[key] = read(depth + 1);
      } while (take(","));
      check(take("}"), "Invalid JSON object.");
      return obj;
    }
    if (take("[")) {
      const arr = [];
      if (take("]")) return arr;
      do {
        arr.push(read(depth + 1));
      } while (take(","));
      check(take("]"), "Invalid JSON array.");
      return arr;
    }
    primitives.lastIndex = i;
    const token = primitives.exec(raw);
    check(token, "Invalid JSON token.");
    i = primitives.lastIndex;
    const result = JSON.parse(token[0]);
    if (typeof result === "number") integer(result);
    return result;
  }
  const obj = read(0);
  space();
  check(i === raw.length, "Unexpected characters after JSON.");
  return obj;
}

function fields(obj, names) {
  check(
    obj &&
      typeof obj === "object" &&
      !Array.isArray(obj) &&
      Object.keys(obj).length === names.length &&
      names.every((n) => Object.hasOwn(obj, n)),
    "Unexpected or missing fields in the trace.",
  );
}
// Match Python str.isspace(), including NEL and excluding the byte order mark.
const nonWhitespace =
  /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
function text(value, blank = false) {
  check(
    typeof value === "string" && (blank || nonWhitespace.test(value)),
    "Expected text.",
  );
}
const same = (a, b) => canonical(a) === canonical(b);
const isHash = (value) =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

async function sourceIndex(sources) {
  check(
    sources && typeof sources === "object" && !Array.isArray(sources),
    "Invalid source collection.",
  );
  const map = new Map();
  for (const [id, s] of Object.entries(sources)) {
    fields(s, ["source_id", "title", "text", "url", "sentences"]);
    text(id);
    text(s.title, true);
    text(s.text);
    if (s.url !== null) text(s.url);
    check(
      id === s.source_id && Array.isArray(s.sentences) && s.sentences.length,
      "Invalid source record.",
    );
    const chars = Array.from(s.text),
      sentences = new Map();
    let end = 0;
    for (const sent of s.sentences) {
      fields(sent, ["sentence_id", "start", "end"]);
      text(sent.sentence_id);
      integer(sent.start, end);
      integer(sent.end, sent.start + 1, chars.length);
      check(
        !sentences.has(sent.sentence_id) &&
          nonWhitespace.test(chars.slice(sent.start, sent.end).join("")),
        "Invalid source sentence.",
      );
      sentences.set(sent.sentence_id, sent);
      end = sent.end;
    }
    map.set(id, { chars, sentences, hash: await digest(s) });
  }
  return map;
}

function bindSpan(span, sources, id, full) {
  fields(span, [
    "source_id",
    "sentence_id",
    "start",
    "end",
    "quote",
    "source_digest",
  ]);
  const source = sources.get(id),
    sent = source?.sentences.get(span.sentence_id);
  check(
    sent &&
      span.source_id === id &&
      span.source_digest === source.hash &&
      span.start === sent.start,
    "Source or sentence identity mismatch.",
  );
  integer(span.end, sent.start + 1, sent.end);
  text(span.quote);
  check(
    (!full || span.end === sent.end) &&
      span.quote === source.chars.slice(span.start, span.end).join(""),
    "Quote differs from its source.",
  );
}

function checkOutput(event, sources, model) {
  const { tool, input, output } = event;
  check(Array.isArray(output), "Invalid tool output.");
  const seen = new Set();
  if (tool === "search") {
    check(output.length <= input.limit, "Search limit exceeded.");
    for (const hit of output) {
      fields(hit, ["source_id", "score", "snippet"]);
      integer(hit.score, 0);
      const source = sources.get(hit.source_id);
      check(
        source &&
          !seen.has(hit.source_id) &&
          source.chars.slice(0, 180).join("") === hit.snippet,
        "Search hit differs from supplied sources.",
      );
      seen.add(hit.source_id);
    }
  } else if (tool === "read") {
    const source = sources.get(input.source_id);
    check(
      source && output.length === Math.min(2, source.sentences.size),
      "Incomplete source read.",
    );
    for (const span of output) {
      bindSpan(span, sources, input.source_id, true);
      check(!seen.has(span.sentence_id), "Duplicate sentence in read.");
      seen.add(span.sentence_id);
    }
  } else {
    check(output.length === input.spans.length, "Missing scored sentence.");
    output.forEach((row, i) => {
      fields(row, [
        "span",
        "contradiction",
        "entailment",
        "neutral",
        "model_digest",
      ]);
      const requested = input.spans[i];
      bindSpan(row.span, sources, requested.source_id, false);
      check(
        row.span.sentence_id === requested.sentence_id &&
          row.span.end <= requested.end &&
          row.model_digest === model,
        "Recorded model or scored sentence differs.",
      );
      const sum = ["contradiction", "entailment", "neutral"].reduce(
        (s, k) => s + integer(row[k], 0, 1000000),
        0,
      );
      check(sum === 1000000, "Invalid score total.");
    });
  }
}

async function replay(run, sources) {
  const { claim, config } = run;
  const counts = () => Object.fromEntries(tools.map((t) => [t, 0]));
  const usage = {
    attempts: counts(),
    successes: counts(),
    unique_sources: 0,
    nli_pairs: 0,
  };
  const scores = [],
    rankings = [],
    read = new Set();
  const search = (mode) => ({
    tool: "search",
    input: { claim, mode, limit: 12 },
    attempt: 1,
  });
  let next = search("relevance"),
    status = "running",
    previous = null;
  for (const [i, e] of run.events.entries()) {
    fields(e, [
      "sequence",
      "tool",
      "input",
      "output",
      "attempt",
      "error",
      "previous_hash",
      "hash",
    ]);
    check(
      status === "running" &&
        next &&
        e.sequence === i + 1 &&
        e.previous_hash === previous &&
        e.tool === next.tool &&
        same(e.input, next.input) &&
        e.attempt === next.attempt,
      "Event sequence or policy differs.",
    );
    const { hash, ...body } = e;
    check((await digest(body)) === hash, "Event hash mismatch.");
    previous = hash;
    usage.attempts[e.tool]++;
    if (e.error !== null) {
      check(
        ["transient", "permanent", "invalid_output"].includes(e.error) &&
          e.output === null,
        "Invalid error event.",
      );
      if (e.error === "transient" && e.attempt < 3)
        next = { ...next, attempt: e.attempt + 1 };
      else status = "failed";
      continue;
    }
    checkOutput(e, sources, run.model_digest);
    usage.successes[e.tool]++;
    if (e.tool === "read") {
      read.add(e.input.source_id);
      usage.unique_sources++;
      next = { tool: "verify", input: { claim, spans: e.output }, attempt: 1 };
      continue;
    }
    if (e.tool === "search") rankings.unshift(e.output);
    else {
      scores.push(...e.output);
      usage.nli_pairs += e.output.length;
    }
    const remaining = rankings.flat().find((hit) => !read.has(hit.source_id));
    next = remaining
      ? {
          tool: "read",
          input: { claim, source_id: remaining.source_id },
          attempt: 1,
        }
      : null;
    const pivot =
      ["challenge", "guarded"].includes(config.policy) &&
      rankings.length === 1 &&
      (read.size >= 3 || !remaining);
    if (read.size >= config.budget) next = null;
    else if (config.policy === "first_hit" || pivot) {
      const { verdict } = await decide(scores, config.threshold);
      if (pivot)
        next = search(verdict === "INSUFFICIENT" ? "focus" : "challenge");
      else if (verdict !== "INSUFFICIENT") next = null;
    }
    if (!next) status = "complete";
  }
  check(
    run.status === status && same(run.scores, scores) && same(run.usage, usage),
    "Recorded scores, usage or status differ from replay.",
  );
  const expected = status === "complete" ? await viewAssessment(run, []) : null;
  check(
    same(run.assessment, expected),
    "Recorded assessment differs from replay.",
  );
}

export async function validateImport(value) {
  check(
    utf8.encode(canonical(value)).length <= MAX_BYTES,
    "The bundle exceeds 5 MiB.",
  );
  fields(value, ["schema_version", "run", "sources", "view"]);
  const { run, view } = value;
  fields(run, [
    "schema_version",
    "run_id",
    "claim",
    "config",
    "corpus_digest",
    "model_digest",
    "status",
    "events",
    "scores",
    "assessment",
    "usage",
  ]);
  fields(run.config, ["policy", "threshold", "budget", "model_digest"]);
  fields(view, ["excluded_source_ids"]);
  check(
    value.schema_version === 1 && run.schema_version === 1,
    "Unsupported schema version.",
  );
  text(run.run_id);
  text(run.claim);
  text(run.model_digest);
  check(
    isHash(run.corpus_digest) && run.model_digest === run.config.model_digest,
    "Invalid corpus or model binding.",
  );
  integer(run.config.threshold, 0, 1000000);
  integer(run.config.budget, 1, 8);
  check(
    ["fixed", "first_hit", "challenge", "guarded"].includes(run.config.policy),
    "Unknown policy.",
  );
  check(
    Array.isArray(run.events) &&
      run.events.length <= (2 + 2 * run.config.budget) * 3 &&
      Array.isArray(run.scores),
    "Invalid trace length.",
  );
  await replay(run, await sourceIndex(value.sources));
  const ids = view.excluded_source_ids,
    scored = new Set(run.scores.map((r) => r.span.source_id));
  check(
    Array.isArray(ids) &&
      ids.length === new Set(ids).size &&
      ids.every((id) => scored.has(id)),
    "Invalid excluded sources.",
  );
  return value;
}

export function bundleFor(run, sources, excluded) {
  const required = new Set();
  for (const e of run.events) {
    if (e.tool === "search" && !e.error)
      for (const hit of e.output) required.add(hit.source_id);
    if (e.tool === "read") required.add(e.input.source_id);
  }
  return {
    schema_version: 1,
    run,
    sources: Object.fromEntries(
      [...required].sort(compareText).map((id) => [id, sources[id]]),
    ),
    view: { excluded_source_ids: [...excluded].sort(compareText) },
  };
}

export function safeUrl(raw) {
  if (
    typeof raw !== "string" ||
    !/^https?:\/\/[^/]/i.test(raw) ||
    /[\s\\<>"'\x60\x00-\x1f\x7f-\x9f]/u.test(raw)
  )
    return null;
  try {
    const u = new URL(raw);
    const authority = raw.slice(raw.indexOf("//") + 2).split(/[/?#]/)[0];
    return u.hostname && !authority.includes("@") && !u.username && !u.password
      ? u.href
      : null;
  } catch {
    return null;
  }
}

function escapeMd(value) {
  return value
    .replace(/[\\\x60*_{}[\]()#+.!|~\->]/g, "\\$&")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
const label = (value) =>
  escapeMd(value.replace(/\r\n|[\n\r\u000b\u000c\u0085\u2028\u2029]/gu, " "));

export async function markdown(bundle) {
  const { run, sources, view } = bundle,
    excluded = new Set(view.excluded_source_ids);
  const current = await viewAssessment(run, excluded);
  const complete = run.status === "complete";
  const lines = [
    "# Claim investigation",
    "",
    label(run.claim),
    "",
    complete
      ? "Original verdict: **" + run.assessment.final.verdict + "**"
      : "Recorded status: **" + run.status + "**",
    "",
    (complete ? "Current audit: **" : "Provisional audit: **") +
      current.final.verdict +
      "**",
    "",
    (complete ? "Base verdict" : "Provisional base verdict") +
      " on retained sources: " +
      current.base.verdict,
    "",
    "Excluded sources: " + ([...excluded].map(label).join(", ") || "none"),
    "",
    "Policy: " +
      run.config.policy +
      ", read budget " +
      run.config.budget +
      ", threshold " +
      run.config.threshold +
      " millionths.",
    "",
    "## Leave-one-source-out results",
    "",
  ];
  for (const [id, d] of Object.entries(current.withdrawals))
    lines.push("- " + label(id) + " → " + d.verdict);
  lines.push("", "## Recorded evidence", "");
  for (const [i, row] of run.scores.entries()) {
    const s = row.span,
      source = sources[s.source_id],
      url = safeUrl(source.url);
    lines.push(
      "### E" + (i + 1) + " · " + label(source.title || source.source_id),
      "",
      (excluded.has(s.source_id) ? "Excluded" : "Included") +
        " · " +
        label(s.source_id) +
        ", sentence " +
        label(s.sentence_id) +
        ", characters [" +
        s.start +
        ", " +
        s.end +
        ").",
      "",
    );
    if (url)
      lines.push("[Source record](<" + url.replaceAll("&", "&amp;") + ">)", "");
    lines.push(
      ...s.quote
        .split(/\r\n|[\n\r\u2028\u2029]/u)
        .map((q) => "> " + escapeMd(q)),
      "",
      "Entailment " +
        row.entailment +
        ", contradiction " +
        row.contradiction +
        ", neutral " +
        row.neutral +
        " (millionths).",
      "",
    );
  }
  lines.push(
    "The automated model scored these excerpts from the supplied corpus. Scores are uncalibrated. Source withdrawal reuses the recorded scores. Annotations and retrieval can miss relevant evidence.",
  );
  return lines.join("\n") + "\n";
}
