// Match Python's sorted-key UTF-8 JSON and integer witness ordering.
export function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

export function integer(
  value,
  min = -Number.MAX_SAFE_INTEGER,
  max = Number.MAX_SAFE_INTEGER,
) {
  requireValue(
    Number.isSafeInteger(value) && value >= min && value <= max,
    "Invalid integer.",
  );
  return value;
}

export function compareText(a, b) {
  const left = a[Symbol.iterator](),
    right = b[Symbol.iterator]();
  while (true) {
    const x = left.next(),
      y = right.next();
    if (x.done || y.done) return Number(y.done) - Number(x.done);
    const diff = x.value.codePointAt(0) - y.value.codePointAt(0);
    if (diff) return diff;
  }
}

export function canonical(value) {
  if (value === null || typeof value === "boolean")
    return JSON.stringify(value);
  if (typeof value === "number") return String(integer(value));
  if (typeof value === "string") {
    requireValue(!/[\uD800-\uDFFF]/u.test(value), "Invalid Unicode surrogate.");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  requireValue(
    typeof value === "object" &&
      [Object.prototype, null].includes(Object.getPrototypeOf(value)),
    "Invalid JSON value.",
  );
  const entries = Object.keys(value)
    .sort(compareText)
    .map((k) => canonical(k) + ":" + canonical(value[k]));
  return "{" + entries.join(",") + "}";
}

export async function digest(value) {
  const bytes = new TextEncoder().encode(canonical(value));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), (x) =>
    x.toString(16).padStart(2, "0"),
  ).join("");
}

function order(a, b) {
  for (let i = 0; i < a.length; i++) {
    const diff =
      typeof a[i] === "string" ? compareText(a[i], b[i]) : a[i] - b[i];
    if (diff) return diff;
  }
  return 0;
}

async function summarize(scores, threshold) {
  integer(threshold, 0, 1000000);
  const support = new Map(),
    refute = new Map(),
    sources = new Set(),
    seen = new Set();
  for (const row of scores) {
    const { source_id, sentence_id, start, end } = row.span;
    const key = canonical([source_id, sentence_id]);
    requireValue(!seen.has(key), "Duplicate scored sentence.");
    seen.add(key);
    sources.add(source_id);
    const values = [row.entailment, row.contradiction, row.neutral].map((v) =>
      integer(v, 0, 1000000),
    );
    requireValue(
      values.reduce((a, b) => a + b, 0) === 1000000,
      "Scores must sum to one million.",
    );
    if (Math.max(values[0], values[1]) < threshold) continue;
    const id = await digest(row.span);
    for (const [value, side] of [
      [values[0], support],
      [values[1], refute],
    ]) {
      if (value < threshold) continue;
      const next = [-value, source_id, sentence_id, start, end, id];
      if (!side.has(source_id) || order(next, side.get(source_id)) < 0)
        side.set(source_id, next);
    }
  }
  return { support, refute, sources };
}

function outcome(verdict, witnesses = [], internal_conflict = false) {
  return { verdict, witnesses, internal_conflict };
}

function decision({ support, refute }, omit) {
  const retained = (side) =>
    [...side.values()].filter((w) => w[1] !== omit).sort(order);
  const pos = retained(support),
    neg = retained(refute);
  if (!pos.length && !neg.length) return outcome("INSUFFICIENT");
  if (!pos.length) return outcome("REFUTES", [neg[0][5]]);
  if (!neg.length) return outcome("SUPPORTS", [pos[0][5]]);
  let best;
  for (const p of pos) {
    for (const n of neg) {
      if (p[1] === n[1]) continue;
      const key = [p[0] + n[0], ...p, ...n];
      if (!best || order(key, best.key) < 0)
        best = { key, witnesses: [p[5], n[5]] };
    }
  }
  if (best) return outcome("CONFLICT", best.witnesses);
  return outcome("INSUFFICIENT", [pos[0][5], neg[0][5]], true);
}

export async function decide(scores, threshold) {
  return decision(await summarize(scores, threshold));
}

export async function audit(scores, threshold) {
  const sides = await summarize(scores, threshold),
    base = decision(sides);
  const entries = [...sides.sources]
    .sort(compareText)
    .map((id) => [id, decision(sides, id)]);
  const pivotal = entries
    .filter(([, v]) => v.verdict !== base.verdict)
    .map(([id]) => id);
  return {
    base,
    withdrawals: Object.fromEntries(entries),
    pivotal_sources: pivotal,
    stable: !pivotal.length,
  };
}

export async function viewAssessment(run, excluded) {
  const omit = new Set(excluded);
  const current = await audit(
    run.scores.filter((r) => !omit.has(r.span.source_id)),
    run.config.threshold,
  );
  const final =
    run.config.policy === "guarded" && !current.stable
      ? outcome("ABSTAIN")
      : current.base;
  return { ...current, final };
}
