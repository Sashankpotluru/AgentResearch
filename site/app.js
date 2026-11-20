import { renderEvaluation } from "./evaluation.js?v=cyber-v2";
import { digest, viewAssessment } from "./decision.js";
import {
  bundleFor,
  markdown,
  MAX_BYTES,
  parseBundle,
  safeUrl,
  validateImport,
} from "./import.js?v=cyber-v2";

const $ = (id) => document.getElementById(id);
const verdicts = {
  SUPPORTS: "Supports",
  REFUTES: "Refutes",
  CONFLICT: "Conflict",
  INSUFFICIENT: "Insufficient",
  ABSTAIN: "Abstain",
};
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
  summary,
  desiredCase;
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
  const res = await fetch(path + "?v=cyber-v1");
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
function link(label, target) {
  const url = safeUrl(target);
  if (!url) return el("span", label);
  const a = el("a", label);
  a.href = url;
  return a;
}
function scoreExcerpt(row) {
  const s = row.span,
    src = bundle.sources[s.source_id];
  const sent = src.sentences.find((x) => x.sentence_id === s.sentence_id);
  const article = el("div", undefined, "excerpt");
  const quote = el("blockquote");
  const markup = record && s.quote.match(/<code>[^<>]+<\/code>/g);
  if (markup) {
    for (const segment of s.quote.split(/(<code>[^<>]+<\/code>)/g)) {
      if (segment.startsWith("<code>") && segment.endsWith("</code>"))
        quote.append(el("code", segment.slice(6, -7)));
      else quote.append(document.createTextNode(segment));
    }
  } else quote.textContent = s.quote;
  const location = details(
    "Score and source location",
    el(
      "p",
      "Entailment " +
        row.entailment +
        " · Contradiction " +
        row.contradiction +
        " · Neutral " +
        row.neutral +
        " (millionths).",
    ),
    el(
      "p",
      s.source_id +
        " · Sentence " +
        s.sentence_id +
        " · Characters [" +
        s.start +
        ", " +
        s.end +
        ")" +
        (s.end < sent.end ? " · Scored prefix" : ""),
      "locator",
    ),
  );
  if (markup)
    location.append(
      el("p", "Verbatim scored text"),
      el("p", s.quote, "context-text"),
    );
  article.append(quote, location);
  return article;
}
function showHeader() {
  $("case-title").textContent = record
    ? record.family
    : "Imported investigation";
  const ctx = $("case-context");
  ctx.replaceChildren();
  if (record) {
    const meta = el("div", undefined, "technique");
    meta.append(
      link(record.family_id, record.family_url),
      el("span", " / "),
      link(
        record.technique_id + " · " + record.technique,
        record.technique_url,
      ),
    );
    ctx.append(meta);
    // Keep the original catalogue description available for families with long entries.
    const first =
      record.description.match(/^.*?[.!?](?=\s|$)/u)?.[0] || record.description;
    ctx.append(el("p", first, "family-description"));
    if (first.length < record.description.length)
      ctx.append(
        details("Full ATT&CK description", el("p", record.description)),
      );
  }
  ctx.append(el("p", bundle.run.claim, "claim"));
}
function showSummary(current) {
  const { run } = bundle,
    panel = $("paper");
  const line = el("div", undefined, "assessment");
  line.append(
    el(
      "h2",
      run.status === "complete"
        ? "Model assessment"
        : "Provisional model assessment",
    ),
    el(
      "strong",
      verdicts[current.final.verdict],
      "outcome " + current.final.verdict.toLowerCase(),
    ),
  );
  const original = run.assessment?.final.verdict;
  let reason =
    original && excluded.size
      ? "Recorded: " +
        verdicts[original] +
        ". After withdrawal: " +
        verdicts[current.final.verdict] +
        ". "
      : "";
  const n = current.pivotal_sources.length;
  if (Object.keys(current.withdrawals).length) {
    reason += n
      ? n +
        (n === 1 ? " source changes" : " sources change") +
        " the base assessment when withdrawn individually."
      : "Withdrawing any one retained source leaves the base assessment unchanged.";
  } else reason += "No scored sources remain in this view.";
  if (current.base.internal_conflict)
    reason += " One source contains opposing model judgments.";
  if (current.final.verdict === "ABSTAIN")
    reason +=
      " Guarded withheld the base assessment of " +
      verdicts[current.base.verdict].toLowerCase() +
      ".";
  panel.replaceChildren(line, el("p", reason));
  panel.append(
    el(
      "p",
      record
        ? "This model can mistake unrelated behavior for a contradiction. Review the cited text before interpreting its assessment."
        : "This view replays the imported model scores at the run’s recorded threshold.",
      "assessment-note",
    ),
  );
}
function showStep(e) {
  const p = $("step-detail");
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
    for (const row of e.output)
      p.append(
        el("strong", sourceTitle(row.span.source_id)),
        scoreExcerpt(row),
      );
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
  else nav.append(button("Run overview", () => selectStep(-1, true)));
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
  const overview = button("Overview", () => selectStep(-1, true));
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
  $("rail-caption").textContent =
    "Run trace · " + run.events.length + " recorded steps";
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
function drawSources(current) {
  const list = $("source-list"),
    groups = new Map(),
    witnesses = new Set(current.base.witnesses);
  for (const [i, row] of bundle.run.scores.entries()) {
    const id = row.span.source_id;
    if (!groups.has(id)) groups.set(id, { id, primary: [], other: [] });
    groups.get(id)[witnesses.has(ids[i]) ? "primary" : "other"].push(row);
  }
  list.replaceChildren();
  $("evidence-note").textContent =
    groups.size +
    " scored " +
    (groups.size === 1 ? "source" : "sources") +
    (record
      ? ". Excerpts quote MITRE procedure summaries grouped by canonical reference URL."
      : ". Excerpts quote the supplied source texts.");
  const ordered = [...groups.values()].sort(
    (a, b) =>
      Number(Boolean(b.primary.length)) - Number(Boolean(a.primary.length)),
  );
  for (const { id, primary, other } of ordered) {
    const off = excluded.has(id),
      src = bundle.sources[id];
    const shelf = el("article", undefined, "source" + (off ? " excluded" : ""));
    const head = el("div", undefined, "source-head");
    const toggle = button(off ? "Restore" : "Withdraw", () => {
      if (off) excluded.delete(id);
      else excluded.add(id);
      render({ source: id }, true);
    });
    toggle.dataset.source = id;
    toggle.setAttribute(
      "aria-label",
      (off ? "Restore " : "Withdraw ") + sourceTitle(id),
    );
    head.append(el("h3", sourceTitle(id)), toggle);
    shelf.append(head);
    if (off)
      shelf.append(
        el("p", "Withdrawn from the current assessment.", "source-state"),
      );
    else {
      if (current.pivotal_sources.includes(id))
        shelf.append(
          el("p", "Withdrawal changes the base assessment", "source-state"),
        );
      const rows = [...primary, ...other];
      shelf.append(scoreExcerpt(rows[0]));
      if (rows.length > 1)
        shelf.append(
          details(
            "More scored excerpts (" + (rows.length - 1) + ")",
            ...rows.slice(1).map(scoreExcerpt),
          ),
        );
    }
    const refs = el("div", undefined, "source-links");
    if (safeUrl(src.url))
      refs.append(
        link(record ? "Open canonical reference ↗" : "Source link ↗", src.url),
      );
    refs.append(details("Full source text", el("p", src.text, "context-text")));
    shelf.append(refs);
    list.append(shelf);
  }
  $("reset").disabled = !excluded.size;
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
  drawSources(current);
  showSummary(current);
  $("step-detail").replaceChildren();
  if (selected < 0) {
    const run = bundle.run;
    $("step-detail").append(
      details(
        "Recorded run identity",
        el("p", "Run: " + run.run_id, "locator"),
        el("p", "Corpus digest: " + run.corpus_digest, "locator"),
        el("p", "Model identifier: " + run.model_digest, "locator"),
      ),
    );
  } else showStep(bundle.run.events[selected]);
  if (focus?.source)
    [...$("source-list").querySelectorAll("button")]
      .find((b) => b.dataset.source === focus.source)
      ?.focus();
  if (focus?.rail !== undefined)
    [...$("steps").children]
      .find((b) => b.dataset.step === String(focus.rail))
      ?.focus();
  if (announce)
    $("audit-status").textContent =
      (bundle.run.status === "complete"
        ? "Current verdict "
        : "Recorded status: " + bundle.run.status + ". Provisional verdict ") +
      verdicts[current.final.verdict] +
      ". " +
      excluded.size +
      (excluded.size === 1 ? " source excluded." : " sources excluded.");
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
function refreshCases() {
  const query = $("case-search").value.trim().toLowerCase();
  const cases = (index?.claims || []).filter((c) =>
    [c.family, c.family_id, c.technique, c.technique_id].some((v) =>
      v.toLowerCase().includes(query),
    ),
  );
  const list = $("case-list");
  list.replaceChildren();
  $("case-count").textContent = query
    ? cases.length + " matching cases"
    : cases.length + " saved cases";
  for (const c of cases) {
    const row = button(
      "",
      () => {
        desiredCase = c.query_id;
        if (!record && bundle) {
          $("policy").value = "challenge";
          $("budget").value = "4";
        }
        return loadClaim("case-title");
      },
      "case-row",
    );
    row.dataset.case = c.query_id;
    row.setAttribute("aria-current", String(c.query_id === record?.query_id));
    row.append(
      el("strong", c.family),
      el("span", c.technique_id),
      el("small", c.technique),
    );
    list.append(row);
  }
  if (!cases.length)
    list.append(el("p", "Try another family name or technique ID.", "empty"));
}
function syncSelectors() {
  const imported = !record;
  desiredCase = record?.query_id;
  $("budget").querySelector("[data-import]")?.remove();
  if (imported && ![4, 8].includes(bundle.run.config.budget)) {
    const budget = String(bundle.run.config.budget);
    const extra = option(budget, budget + " sources");
    extra.dataset.import = "";
    $("budget").add(extra);
  }
  $("policy").value = bundle.run.config.policy;
  $("budget").value = String(bundle.run.config.budget);
  $("policy").disabled = imported;
  $("budget").disabled = imported;
  refreshCases();
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
  showHeader();
  $("trace").open = false;
  drawAssessment(current, null, true);
  setAvailable(true);
  note(record ? "" : "Imported run checked against its supplied sources.");
}
const caseTextFields = [
  "query_id",
  "claim",
  "family",
  "family_id",
  "technique",
  "technique_id",
];
function hasCaseIdentity(row) {
  return (
    row &&
    caseTextFields.every(
      (field) => typeof row[field] === "string" && row[field].trim().length > 0,
    ) &&
    typeof row.ransomware_related === "boolean"
  );
}
function validateCaseRecord(next, item) {
  if (
    !hasCaseIdentity(next) ||
    caseTextFields.some((field) => next[field] !== item[field]) ||
    next.ransomware_related !== item.ransomware_related ||
    next.schema_version !== 1 ||
    typeof next.description !== "string" ||
    next.source_note !== index.source_note ||
    next.family_url !==
      "https://attack.mitre.org/software/" + item.family_id + "/" ||
    next.technique_url !==
      "https://attack.mitre.org/techniques/" +
        item.technique_id.replace(".", "/") +
        "/"
  )
    throw new Error("Case metadata differs from the release.");
}
async function loadClaim(focusId = null) {
  const item = index?.claims.find((c) => c.query_id === desiredCase);
  if (!item) return;
  const token = beginRequest("Loading claim " + item.query_id + "…");
  const policy = $("policy").value,
    budget = $("budget").value;
  try {
    const next =
      record?.query_id === item.query_id
        ? record
        : await getJSON("data/" + item.path);
    if (token !== request) return;
    validateCaseRecord(next, item);
    const run = next.runs[policy + "-" + budget];
    if (
      run.corpus_digest !== index.corpus_digest ||
      run.model_digest !== index.model_digest ||
      run.config.threshold !== index.threshold ||
      run.config.policy !== policy ||
      String(run.config.budget) !== budget ||
      run.claim !== item.claim
    )
      throw new Error("Claim or release identity mismatch.");
    await activate(bundleFor(run, next.sources, []), next, token);
    if (focusId && token === request) {
      if (focusId === "case-title" && narrow.matches) $("chooser").open = false;
      $(focusId).focus();
    }
  } catch (err) {
    await restoreActive(
      token,
      "Could not open this recorded run. " + err.message,
    );
  }
}
$("case-search").addEventListener("input", refreshCases);
$("policy").addEventListener("change", () => loadClaim("policy"));
$("budget").addEventListener("change", () => loadClaim("budget"));
$("reset").addEventListener("click", () => {
  excluded.clear();
  selected = -1;
  render(null, true);
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

const narrow = window.matchMedia("(max-width: 740px)");
function sizeChooser() {
  $("chooser").open = !narrow.matches;
}
sizeChooser();
narrow.addEventListener("change", sizeChooser);
const views = ["investigations", "evaluation", "method"];
function showView() {
  const hash = window.location.hash.slice(1);
  if (hash === "main") return;
  const active = views.includes(hash) ? hash : "investigations";
  for (const view of views) {
    $(view).hidden = view !== active;
    $("nav-" + view).setAttribute(
      "aria-current",
      view === active ? "page" : "false",
    );
  }
  document.title =
    "AgentResearch · " +
    (active === "investigations"
      ? "Malware investigations"
      : active[0].toUpperCase() + active.slice(1));
}
window.addEventListener("hashchange", showView);
showView();
function showResults() {
  if (summary)
    renderEvaluation(
      summary,
      $("results"),
      $("agent-results"),
      $("cohort").value,
      $("result-budget").value,
    );
}
$("cohort").addEventListener("change", showResults);
$("result-budget").addEventListener("change", showResults);
async function start() {
  try {
    const candidate = await getJSON("data/index.json");
    if (
      !Array.isArray(candidate.claims) ||
      !candidate.claims.length ||
      typeof candidate.source_note !== "string" ||
      !candidate.claims.every(
        (item) =>
          hasCaseIdentity(item) &&
          item.path === "claims/" + item.query_id + ".json",
      )
    ) {
      throw new Error("Invalid case index metadata.");
    }
    index = candidate;
    refreshCases();
    if (!bundle && !request) {
      desiredCase = (
        index.claims.find((c) => c.family === "WannaCry") || index.claims[0]
      ).query_id;
      await loadClaim();
    }
  } catch (err) {
    note("Could not load the case library. " + err.message, true);
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
      "Could not load the measured results. Open the full results below.";
  });
