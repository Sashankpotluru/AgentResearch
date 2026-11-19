import { digest, viewAssessment } from "./decision.js";
import {
  bundleFor,
  markdown,
  MAX_BYTES,
  parseBundle,
  safeUrl,
  validateImport,
} from "./import.js";

const $ = (id) => document.getElementById(id);
const policies = {
  fixed: "Fixed",
  first_hit: "First hit",
  challenge: "Challenge",
  guarded: "Guarded",
};
const verdicts = {
  SUPPORTS: "Supports",
  REFUTES: "Refutes",
  CONFLICT: "Conflict",
  INSUFFICIENT: "Insufficient",
  ABSTAIN: "Abstain",
};
const labels = Object.keys({
  SUPPORTS: 0,
  REFUTES: 1,
  INSUFFICIENT: 2,
  CONFLICT: 3,
  ABSTAIN: 4,
});
const toolNames = {
  search: "Search",
  read: "Read source",
  verify: "Score excerpts",
};
let index,
  record,
  bundle,
  selected = -1,
  excluded = new Set(),
  ids = [],
  request = 0,
  paint = 0,
  summary;
const el = (tag, content, cls) => {
  const n = document.createElement(tag);
  if (content !== undefined) n.textContent = content;
  if (cls) n.className = cls;
  return n;
};
function button(name, action, cls) {
  const b = el("button", name, cls);
  b.type = "button";
  b.addEventListener("click", action);
  return b;
}
function details(name, ...children) {
  const d = el("details", undefined, "notes");
  d.append(el("summary", name), ...children);
  return d;
}
function sourceTitle(id) {
  return bundle.sources[id]?.title || id;
}
function note(message = "", error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}
async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok)
    throw new Error("Could not load " + path + " (HTTP " + res.status + ").");
  return res.json();
}
function currentBundle() {
  return { ...bundle, view: { excluded_source_ids: [...excluded] } };
}
function option(value, label) {
  return new Option(label, value);
}
function exportFile(body, name, type) {
  const file = new Blob([body], { type });
  if (type === "application/json" && file.size > MAX_BYTES) {
    $("exports").open = false;
    note(
      "This view exceeds the 5 MiB portable limit. Restore sources to shorten the exclusion list, or export the Markdown report.",
      true,
    );
    return;
  }
  const url = URL.createObjectURL(file),
    a = el("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $("exports").open = false;
}
function scoreExcerpt(row) {
  const s = row.span,
    src = bundle.sources[s.source_id];
  const article = el("div", undefined, "excerpt");
  article.append(
    el("p", sourceTitle(s.source_id), "source-line"),
    el("blockquote", s.quote),
  );
  const sent = src.sentences.find((x) => x.sentence_id === s.sentence_id);
  const provenance =
    s.source_id +
    " · Sentence " +
    s.sentence_id +
    " · Characters [" +
    s.start +
    ", " +
    s.end +
    ")";
  article.append(
    el(
      "p",
      provenance +
        (s.end < sent.end ? " · Scored prefix of the original sentence" : ""),
      "locator",
    ),
    el(
      "p",
      "Entailment " +
        row.entailment.toLocaleString("en-US") +
        " · Contradiction " +
        row.contradiction.toLocaleString("en-US") +
        " · Neutral " +
        row.neutral.toLocaleString("en-US"),
      "score-line",
    ),
  );
  const context = details(
    "Original source text",
    el("p", src.text, "context-text"),
  );
  const url = safeUrl(src.url);
  if (url) {
    const a = el("a", "Open source record ↗");
    a.href = url;
    context.append(a);
  }
  article.append(context);
  return article;
}
function showSummary(current) {
  const { run } = bundle,
    p = $("paper"),
    original = run.assessment?.final.verdict;
  p.append(
    el(
      "div",
      record
        ? "SciFact / Development claim " + record.query_id
        : "Imported investigation",
      "record-label",
    ),
    el("h3", run.claim),
  );
  const result = el("div", undefined, "assessment");
  const prefix =
    run.status === "complete" ? "Current verdict" : "Provisional verdict";
  result.append(
    el("strong", prefix),
    el(
      "span",
      verdicts[current.final.verdict],
      "outcome " + current.final.verdict.toLowerCase(),
    ),
  );
  p.append(result);
  const m =
    "Recorded verdict: " +
    (verdicts[original] ?? run.status) +
    (record ? " · Dataset label: " + verdicts[record.label] : "");
  p.append(el("p", m, "annotation"));
  let reason = current.stable
    ? "Removing any one retained source leaves the base verdict unchanged."
    : current.pivotal_sources.length +
      " pivotal " +
      (current.pivotal_sources.length === 1
        ? "source changes"
        : "sources change") +
      " the base verdict when withdrawn individually.";
  if (!Object.keys(current.withdrawals).length)
    reason = "The current audit contains no scored sources.";
  if (current.base.internal_conflict)
    reason += " One source supplies opposing judgments.";
  if (current.final.verdict === "ABSTAIN")
    reason +=
      " Guarded abstains from the base verdict of " +
      verdicts[current.base.verdict].toLowerCase() +
      ".";
  p.append(el("p", reason, "summary-line"));
  const evidence = el("section", undefined, "evidence-section");
  const witnessSet = new Set(current.base.witnesses);
  const retained = run.scores.filter(
    (row) => !excluded.has(row.span.source_id),
  );
  const witnesses = run.scores.filter(
    (row, i) => witnessSet.has(ids[i]) && !excluded.has(row.span.source_id),
  );
  const leading = witnesses.length ? witnesses : retained.slice(0, 1);
  evidence.append(
    el("h4", witnesses.length ? "Verdict evidence" : "Recorded evidence"),
  );
  if (leading.length) {
    evidence.append(
      el(
        "p",
        "Exact scored excerpts. Model scores are uncalibrated millionths; the threshold is " +
          run.config.threshold.toLocaleString("en-US") +
          ".",
        "meta",
      ),
    );
    for (const row of leading) evidence.append(scoreExcerpt(row));
    const rest = retained.filter((row) => !leading.includes(row));
    if (rest.length)
      evidence.append(
        details(
          "Other scored excerpts (" + rest.length + ")",
          ...rest.map(scoreExcerpt),
        ),
      );
  } else
    evidence.append(
      el(
        "p",
        "Restore a scored source to inspect its excerpts. The run trace retains the original tool outputs.",
        "subtle",
      ),
    );
  p.append(
    evidence,
    details(
      "Recorded run identity",
      el("p", "Run: " + run.run_id, "locator"),
      el("p", "Corpus digest: " + run.corpus_digest, "locator"),
      el("p", "Model identifier: " + run.model_digest, "locator"),
    ),
  );
}
function showStep(e) {
  const p = $("paper");
  p.append(
    el(
      "div",
      "Recorded tool step " + e.sequence + " of " + bundle.run.events.length,
      "record-label",
    ),
    el(
      "h3",
      e.tool === "search"
        ? "Search the corpus"
        : e.tool === "read"
          ? "Read source sentences"
          : "Score the excerpts",
    ),
  );
  if (e.error)
    p.append(el("p", "Attempt " + e.attempt + " ended with " + e.error + "."));
  else if (e.tool === "search") {
    p.append(
      el(
        "p",
        e.input.mode + " search · " + e.output.length + " ranked sources",
        "summary-line",
      ),
      el("p", e.input.claim, "meta"),
    );
    const list = el("ol", undefined, "hit-list");
    for (const h of e.output) {
      const item = el("li");
      item.append(
        el("strong", sourceTitle(h.source_id)),
        el("p", h.snippet),
        el(
          "small",
          h.source_id + " · BM25 " + (h.score / 1000000).toFixed(4),
          "meta",
        ),
      );
      list.append(item);
    }
    p.append(list);
  } else if (e.tool === "read") {
    p.append(
      el("p", sourceTitle(e.input.source_id), "summary-line"),
      el("p", e.input.source_id, "meta"),
    );
    for (const s of e.output) {
      const box = el("div", undefined, "excerpt");
      box.append(
        el(
          "p",
          "Sentence " + s.sentence_id + " · [" + s.start + ", " + s.end + ")",
          "locator",
        ),
        el("blockquote", s.quote),
      );
      p.append(box);
    }
  } else {
    p.append(
      el(
        "p",
        "Recorded neural inference · Uncalibrated integer millionths",
        "meta",
      ),
    );
    for (const row of e.output) p.append(scoreExcerpt(row));
  }
  p.append(
    details(
      "Event identity",
      el("p", "SHA-256 " + e.hash, "locator"),
      el("p", "Attempt " + e.attempt, "locator"),
    ),
  );
  const nav = el("div", undefined, "step-nav");
  nav.append(button("Previous", () => selectStep(selected - 1, true)));
  if (selected + 1 < bundle.run.events.length)
    nav.append(button("Next step", () => selectStep(selected + 1, true)));
  else nav.append(button("View assessment", () => selectStep(-1, true)));
  p.append(nav);
}
function selectStep(i, focus = false) {
  selected = i;
  render(focus ? { rail: i } : null);
}
function drawRail() {
  const { run } = bundle,
    steps = $("steps");
  steps.replaceChildren();
  const overview = button("Assessment", () => selectStep(-1, true));
  overview.dataset.step = "-1";
  overview.setAttribute("aria-current", String(selected === -1));
  steps.append(overview);
  for (const [i, e] of run.events.entries()) {
    const b = button("", () => selectStep(i, true));
    b.dataset.step = String(i);
    b.setAttribute("aria-current", String(i === selected));
    b.append(
      el("span", String(e.sequence), "seq"),
      el("span", toolNames[e.tool]),
    );
    steps.append(b);
  }
  $("rail-caption").textContent = run.events.length + " recorded steps";
  const u = run.usage;
  $("cost").textContent =
    u.attempts.search +
    " searches · " +
    u.attempts.read +
    " reads · " +
    u.attempts.verify +
    " verifications · " +
    u.nli_pairs +
    " NLI pairs";
}
function drawDrawer(current) {
  const list = $("drawer-sources"),
    { run } = bundle,
    counts = new Map();
  list.replaceChildren();
  for (const r of run.scores)
    counts.set(r.span.source_id, (counts.get(r.span.source_id) || 0) + 1);
  for (const [id, n] of counts) {
    const off = excluded.has(id),
      shelf = el("div", undefined, "drawer-shelf" + (off ? " excluded" : ""));
    shelf.append(
      el("span", sourceTitle(id), "source-name"),
      el(
        "span",
        id + " · " + n + " scored " + (n === 1 ? "excerpt" : "excerpts"),
        "drawer-meta",
      ),
    );
    const actions = el("div", undefined, "drawer-actions");
    const toggle = button(off ? "Restore" : "Withdraw", () => {
      if (off) excluded.delete(id);
      else excluded.add(id);
      selected = -1;
      render({ source: id }, true);
    });
    toggle.dataset.source = id;
    toggle.setAttribute("aria-label", (off ? "Restore " : "Withdraw ") + id);
    actions.append(toggle);
    if (current.pivotal_sources.includes(id))
      actions.append(el("span", "Pivotal", "pivotal"));
    if (off) actions.append(el("small", "Excluded", "subtle"));
    shelf.append(actions);
    list.append(shelf);
  }
  if (!counts.size)
    list.append(el("p", "This run contains no scored sources.", "subtle"));
  $("reset").disabled = excluded.size === 0;
}
async function render(focus, announce = false) {
  const stamp = ++paint,
    active = bundle;
  if (!active) return;
  const current = await viewAssessment(active.run, excluded);
  if (stamp !== paint || active !== bundle) return;
  drawAssessment(current, focus, announce);
}
function drawAssessment(current, focus, announce) {
  drawRail();
  drawDrawer(current);
  $("paper").replaceChildren();
  if (selected < 0) showSummary(current);
  else showStep(active.run.events[selected]);
  if (focus?.source)
    [...$("drawer-sources").querySelectorAll("button")]
      .find((b) => b.dataset.source === focus.source)
      ?.focus();
  if (focus?.rail !== undefined)
    [...$("steps").children]
      .find((b) => b.dataset.step === String(focus.rail))
      ?.focus();
  if (announce)
    $("audit-status").textContent =
      "Current verdict " +
      verdicts[current.final.verdict] +
      ". " +
      excluded.size +
      " sources excluded.";
}
function setAvailable(ready) {
  $("workspace").hidden = !ready;
  $("save-json").disabled = !ready;
  $("save-md").disabled = !ready;
  if (!ready) {
    $("exports").open = false;
    $("audit-status").textContent = "";
  }
}
function beginRequest(message) {
  ++paint;
  setAvailable(false);
  note(message);
  return ++request;
}
function syncSelectors() {
  const imported = !record;
  $("claim").querySelector('option[value="import"]')?.remove();
  $("budget").querySelector("[data-import]")?.remove();
  if (imported) {
    $("claim").add(option("import", "Imported · " + bundle.run.claim));
    if (![4, 8].includes(bundle.run.config.budget)) {
      const budget = String(bundle.run.config.budget);
      const extra = option(budget, budget + " sources");
      extra.dataset.import = "";
      $("budget").add(extra);
    }
  }
  $("claim").value = imported ? "import" : record.query_id;
  $("policy").value = bundle.run.config.policy;
  $("budget").value = String(bundle.run.config.budget);
  $("policy").disabled = imported;
  $("budget").disabled = imported;
  $("claim").disabled = !index;
}
async function restoreActive(token, message, error = true) {
  if (token !== request) return;
  if (bundle) {
    const current = await viewAssessment(bundle.run, excluded);
    if (token !== request) return;
    syncSelectors();
    drawAssessment(current, null, true);
    setAvailable(true);
  }
  note(message, error);
}
async function activate(candidate, claimRecord, token) {
  await validateImport(candidate);
  if (token !== request) return;
  const [hashIds, current] = await Promise.all([
    Promise.all(candidate.run.scores.map((row) => digest(row.span))),
    viewAssessment(candidate.run, candidate.view.excluded_source_ids),
  ]);
  if (token !== request) return;
  // Publish the selection, rendered paper and exportable bundle together.
  bundle = candidate;
  record = claimRecord;
  ids = hashIds;
  excluded = new Set(candidate.view.excluded_source_ids);
  selected = -1;
  syncSelectors();
  drawAssessment(current, null, true);
  setAvailable(true);
  note(record ? "" : "Imported run checked against its supplied sources.");
}
async function loadClaim() {
  const id = $("claim").value;
  if (id === "import" && bundle && !record) {
    await restoreActive(
      beginRequest("Opening imported run…"),
      "Imported run checked against its supplied sources.",
      false,
    );
    return;
  }
  const item = index?.claims.find((c) => c.query_id === id);
  if (!item) return;
  const token = beginRequest("Loading claim " + item.query_id + "…");
  if (!record && bundle) $("budget").value = "4";
  const policy = $("policy").value,
    budget = $("budget").value;
  try {
    const next =
      record?.query_id === item.query_id
        ? record
        : await getJSON("data/" + item.path);
    if (token !== request) return;
    const run = next.runs[policy + "-" + budget];
    if (
      run.corpus_digest !== index.corpus_digest ||
      run.model_digest !== index.model_digest ||
      run.config.threshold !== index.threshold ||
      run.config.policy !== policy ||
      String(run.config.budget) !== budget ||
      run.claim !== item.claim ||
      next.query_id !== item.query_id
    )
      throw new Error("Claim or release identity mismatch.");
    await activate(bundleFor(run, next.sources, []), next, token);
  } catch (err) {
    await restoreActive(
      token,
      "Could not open this recorded run. " + err.message,
    );
  }
}
$("claim").addEventListener("change", loadClaim);
$("policy").addEventListener("change", loadClaim);
$("budget").addEventListener("change", loadClaim);
$("reset").addEventListener("click", () => {
  excluded.clear();
  selected = -1;
  render({ rail: -1 }, true);
});
$("import-button").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const token = beginRequest("Checking imported run…");
  try {
    if (file.size > MAX_BYTES)
      throw new Error("Choose a JSON file up to 5 MiB.");
    const raw = new TextDecoder("utf-8", { fatal: true }).decode(
      await file.arrayBuffer(),
    );
    await activate(parseBundle(raw), null, token);
  } catch (err) {
    await restoreActive(token, "Import failed. " + err.message);
  }
});
$("save-json").addEventListener("click", () => {
  if ($("save-json").disabled) return;
  exportFile(
    JSON.stringify(currentBundle()),
    "agentresearch-run.json",
    "application/json",
  );
});
$("save-md").addEventListener("click", async () => {
  if ($("save-md").disabled) return;
  const token = request,
    report = await markdown(currentBundle());
  if (token === request)
    exportFile(
      report,
      "agentresearch-report.md",
      "text/markdown;charset=utf-8",
    );
});

function table(headers, rows) {
  const outer = el("div", undefined, "table-wrap");
  outer.tabIndex = 0;
  outer.setAttribute("role", "region");
  outer.setAttribute("aria-label", headers.join(", "));
  const t = el("table"),
    head = el("thead"),
    body = el("tbody"),
    tr = el("tr");
  for (const h of headers) {
    const cell = el("th", h);
    cell.scope = "col";
    tr.append(cell);
  }
  head.append(tr);
  t.append(head, body);
  for (const row of rows) {
    const tr = el("tr");
    row.forEach((v, i) => {
      const cell = el(i ? "td" : "th", v);
      if (!i) cell.scope = "row";
      tr.append(cell);
    });
    body.append(tr);
  }
  outer.append(t);
  return outer;
}
const pct = (n) => (n === null ? "Undefined" : (n * 100).toFixed(1) + "%");
const pp = (n) =>
  n === null ? "Undefined" : (n > 0 ? "+" : "") + (n * 100).toFixed(2);
function showResults() {
  if (!summary) return;
  const key = $("dataset").value,
    b = $("result-budget").value,
    ds = summary.datasets[key],
    box = $("results");
  box.replaceChildren();
  box.append(
    el(
      "p",
      ds.queries.toLocaleString("en-US") +
        " held-out claims · " +
        ds.clusters +
        " evidence-sharing components · Threshold " +
        summary.threshold.toLocaleString("en-US") +
        " millionths",
      "meta",
    ),
  );
  const main = Object.entries(policies).map(([key, name]) => {
    const v = ds.configs[key + "-" + b];
    return [
      name,
      pct(v.accuracy),
      v.macro_f1.toFixed(3),
      pct(v.coverage),
      pct(v.risk),
      v.mean_reads.toFixed(2),
      v.mean_nli_pairs.toFixed(2),
    ];
  });
  box.append(
    table(
      [
        "Policy",
        "Accuracy",
        "Macro F1",
        "Coverage",
        "Error if covered",
        "Mean reads",
        "Mean NLI pairs",
      ],
      main,
    ),
    el(
      "p",
      "Coverage includes insufficient-evidence decisions. Conditional error counts mistakes among covered claims. Reads and sentence pairs are logical costs per claim.",
      "results-note",
    ),
  );
  const contrast = ds.contrasts["challenge-" + b + "_minus_fixed-" + b];
  const diff = contrast.difference.macro_f1;
  box.append(
    el("h3", "Challenge versus fixed"),
    el(
      "p",
      "At this budget, challenge " +
        (diff < 0
          ? "scored lower than"
          : diff > 0
            ? "scored higher than"
            : "tied") +
        " fixed on macro F1. Differences below are percentage points with paired 95% bootstrap intervals.",
      "results-note",
    ),
  );
  const comparisons = [];
  for (const [key, method] of Object.entries(contrast.intervals)) {
    for (const [k, name] of [
      ["macro_f1", "Macro F1"],
      ["accuracy", "Accuracy"],
    ]) {
      const ci = method[k];
      comparisons.push([
        name,
        key === "component" ? "Component" : "Claim",
        pp(contrast.difference[k]),
        pp(ci.low) + " to " + pp(ci.high),
        String(ci.defined_draws),
      ]);
    }
  }
  box.append(
    table(
      [
        "Metric",
        "Sampling unit",
        "Difference (pp)",
        "95% interval (pp)",
        "Defined draws",
      ],
      comparisons,
    ),
  );
  if (key === "climate")
    box.append(
      el(
        "p",
        "The largest component links " +
          ds.largest_cluster.toLocaleString("en-US") +
          " claims through shared evidence. This concentration limits the component interval. Claim-level resampling gives a sensitivity check.",
        "results-note",
      ),
    );
  const c = ds.configs["challenge-" + b],
    misses = c.confusion
      .map((row, i) => ({
        name: verdicts[labels[i]],
        count: row.reduce((s, x) => s + x, 0) - row[i],
        total: row.reduce((s, x) => s + x, 0),
      }))
      .sort((a, b) => b.count - a.count)[0];
  box.append(
    el(
      "p",
      "Where challenge missed: " +
        misses.count +
        " of " +
        misses.total +
        " claims labeled " +
        misses.name.toLowerCase() +
        " received another verdict. This class had the most mismatches by count.",
      "failure",
    ),
  );
  const extra = Object.entries(policies).map(([id, name]) => {
    const v = ds.configs[id + "-" + b];
    return [
      name,
      pct(v.sentence_recall),
      pct(v.complete_rationale_rate),
      pct(v.quote_integrity),
      pct(v.pivotal_rate),
      v.mean_searches.toFixed(2),
      v.mean_verifies.toFixed(2),
    ];
  });
  box.append(
    details(
      "Evidence retrieval and source dependence",
      table(
        [
          "Policy",
          "Sentence recall",
          "Full rationale",
          "Exact quotes",
          "Pivotal source",
          "Mean searches",
          "Mean verifications",
        ],
        extra,
      ),
      el(
        "p",
        "Sentence recall credits fully retained annotated evidence. Exact quote binding checks literal text. Annotations can omit relevant material.",
        "results-note",
      ),
    ),
  );
}
$("dataset").addEventListener("change", showResults);
$("result-budget").addEventListener("change", showResults);
async function start() {
  try {
    index = await getJSON("data/index.json");
    for (const c of index.claims)
      $("claim").add(option(c.query_id, "#" + c.query_id + " · " + c.claim));
    $("claim").disabled = false;
    if (!bundle && !request) await loadClaim();
  } catch (err) {
    note("Could not load the SciFact index. " + err.message, true);
  }
}
start();
getJSON("data/summary.json")
  .then((data) => {
    summary = data;
    showResults();
  })
  .catch(() => {
    $("results").textContent =
      "The measured summary could not be loaded. Open the full release below for the published results.";
  });
