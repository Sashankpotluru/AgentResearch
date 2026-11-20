const names = {
  bm25: "Word BM25",
  family_bm25: "Family-filtered BM25",
  char_tfidf: "Character TF-IDF",
  rrf: "Reciprocal rank fusion",
};
const policies = {
  fixed: "Fixed",
  first_hit: "First hit",
  challenge: "Challenge",
  guarded: "Guarded",
};
const n = (value) => value.toLocaleString("en-US");
const percent = (value) => (100 * value).toFixed(3) + "%";
const points = (value) => (value > 0 ? "+" : "") + (100 * value).toFixed(3);
function element(tag, content) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  return node;
}
function table(label, headers, rows) {
  const region = element("div"),
    grid = element("table");
  region.className = "table-wrap";
  region.tabIndex = 0;
  region.setAttribute("role", "region");
  region.setAttribute("aria-label", label);
  const head = element("thead"),
    titles = element("tr"),
    body = element("tbody");
  for (const title of headers) {
    const cell = element("th", title);
    cell.scope = "col";
    titles.append(cell);
  }
  head.append(titles);
  for (const row of rows) {
    const tr = element("tr");
    row.forEach((value, i) => {
      const cell = element(i ? "td" : "th", value);
      if (!i) cell.scope = "row";
      tr.append(cell);
    });
    body.append(tr);
  }
  grid.append(head, body);
  region.append(grid);
  return region;
}
function retrieval(title, dataset, text) {
  const { all, ransomware_related: slice } = dataset;
  const section = element("section"),
    paired = all.paired_recall_at_5;
  const rows = Object.entries(all.metrics).map(([method, m]) => [
    names[method],
    percent(m["recall@5"]),
    percent(m["hit@1"]),
    percent(m["hit@5"]),
    percent(slice.metrics[method]["recall@5"]),
  ]);
  const [a, b] = paired.comparison.split(" minus ");
  section.append(
    element("h2", title),
    element("p", text),
    table(
      title,
      [
        "Method",
        "Recall@5 · " + n(all.queries),
        "Hit@1",
        "Hit@5",
        "Ransomware slice recall@5 · " + n(slice.queries),
      ],
      rows,
    ),
    element(
      "p",
      names[a] +
        " minus " +
        names[b] +
        " on overall Recall@5: " +
        points(paired.difference) +
        " percentage points (paired 95% interval " +
        paired.ci95.map(points).join(" to ") +
        ").",
    ),
  );
  return section;
}
export function renderEvaluation(summary, box, agentBox, cohort, budget) {
  const { attack, tram, tram_id_masked: masked } = summary.retrieval;
  box.replaceChildren(
    retrieval(
      "ATT&CK canonical source-record Recall@5",
      attack,
      "Rank canonical source records for 10,739 family–technique queries across 747 families. Duplicate summaries pool references, so the selected URL can differ from a relationship’s own citation. The family filter uses known source-record membership. The 1,527-query slice matches “ransom” in family descriptions. These queries and answers derive from the same catalogue.",
    ),
    retrieval(
      "TRAM technique ranking",
      tram,
      "Rank 708 technique definitions for 4,036 unique labeled sentences from 149 reports. Character TF-IDF had the highest mean Recall@5. The 557-sentence slice matches “ransom” in report titles. Empty labels were excluded.",
    ),
    retrieval(
      "TRAM with literal technique IDs masked",
      masked,
      "Post-hoc diagnostic on the same sentences. Literal technique IDs were removed from scoring text after the original results were reviewed. Parameters, candidate definitions and gold labels stayed fixed. This is a sensitivity check on the original benchmark.",
    ),
  );
  const rows = Object.entries(policies).map(([id, title]) => {
    const r = summary.agent[cohort][id + "-" + budget],
      v = r.verdicts;
    return [
      title,
      r.cases,
      v.SUPPORTS,
      v.REFUTES,
      v.CONFLICT,
      v.INSUFFICIENT,
      v.ABSTAIN,
      r.pivotal_cases,
      (r.reads / r.cases).toFixed(2),
      (r.verifications / r.cases).toFixed(2),
      (r.nli_pairs / r.cases).toFixed(2),
    ];
  });
  agentBox.replaceChildren(
    table(
      "Recorded model outcomes and mean logical costs",
      [
        "Policy",
        "Cases",
        "Supports",
        "Refutes",
        "Conflict",
        "Insufficient",
        "Abstains",
        "Pivotal",
        "Reads/case",
        "Verifications/case",
        "NLI pairs/case",
      ],
      rows,
    ),
  );
}
