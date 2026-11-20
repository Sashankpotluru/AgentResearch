// Exercise the entry module through the same change/click events as the desk.
import assert from "node:assert/strict";
import { resolveObjectURL } from "node:buffer";
import { readFileSync } from "node:fs";
import { setImmediate } from "node:timers/promises";
import test from "node:test";

const site = new URL("../../site/", import.meta.url);
const index = JSON.parse(readFileSync(new URL("data/index.json", site)));
const [original, next] = index.claims;

class Element {
  constructor(tag, downloads) {
    this.tag = tag;
    this.downloads = downloads;
    this.value = "";
    this.children = [];
    this.listeners = new Map();
    this.dataset = {};
    this.attributes = {};
    this.classList = { toggle() {} };
  }
  append(...items) {
    for (const n of items) {
      n.parent = this;
      this.children.push(n);
    }
  }
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  add(option) {
    this.append(option);
    if (!this.value) this.value = option.value;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  querySelectorAll(tag) {
    return this.children.flatMap((n) => [
      ...(n.tag === tag ? [n] : []),
      ...n.querySelectorAll(tag),
    ]);
  }
  focus() {}
  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }
  fire(name) {
    return this.listeners.get(name)?.({ target: this });
  }
  click() {
    if (this.disabled) return;
    if (this.tag === "a") this.downloads.push(resolveObjectURL(this.href));
    return this.fire("click");
  }
  remove() {
    if (this.parent)
      this.parent.children = this.parent.children.filter((n) => n !== this);
  }
  querySelector(selector) {
    if (selector.includes("data-import"))
      return this.children.find((c) => Object.hasOwn(c.dataset, "import"));
    return this.children.find((c) => c.value === "import");
  }
}

async function until(condition) {
  const end = Date.now() + 4000;
  while (!condition()) {
    assert.ok(Date.now() < end, "Desk state did not settle");
    await setImmediate();
  }
}

async function desk(t, changeFetchedCase) {
  const nodes = new Map(),
    downloads = [];
  function get(id) {
    if (!nodes.has(id)) nodes.set(id, new Element(id, downloads));
    return nodes.get(id);
  }
  get("workspace").hidden = true;
  get("save-json").disabled = get("save-md").disabled = true;
  get("budget").value = "4";
  get("policy").value = "challenge";
  get("cohort").value = "benchmark";
  get("result-budget").value = "8";
  const document = {
    getElementById: get,
    createElement: (tag) => new Element(tag, downloads),
    createTextNode: (text) =>
      Object.assign(new Element("#text", downloads), { textContent: text }),
    body: new Element("body", downloads),
  };
  let release,
    waiting = false;
  const delayed = new Promise((resolve) => {
    release = resolve;
  });
  const testGlobals = {
    document,
    window: {
      location: { hash: "" },
      addEventListener: (name, fn) => get("window").addEventListener(name, fn),
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
    Option: class extends Element {
      constructor(label, value) {
        super("option", downloads);
        this.textContent = label;
        this.value = value;
      }
    },
    fetch: async (path) => {
      assert.match(path, /\?v=cyber-v1$/);
      path = path.split("?")[0];
      if (path === "data/" + next.path) {
        waiting = true;
        await delayed;
      }

      return {
        ok: true,
        json: async () => {
          const data = JSON.parse(readFileSync(new URL(path, site)));
          if (path === "data/" + next.path) changeFetchedCase?.(data);
          return data;
        },
      };
    },
  };
  for (const [k, v] of Object.entries(testGlobals)) {
    const original = Object.getOwnPropertyDescriptor(globalThis, k);
    Object.defineProperty(globalThis, k, {
      configurable: true,
      writable: true,
      value: v,
    });
    t.after(() =>
      original
        ? Object.defineProperty(globalThis, k, original)
        : delete globalThis[k],
    );
  }
  await import("../../site/app.js?test=" + encodeURIComponent(t.name));
  await until(() => !get("workspace").hidden && get("paper").children.length);
  return {
    get,
    downloads,
    release,
    switchClaim() {
      const done = get("case-list")
        .children.find((c) => c.dataset.case === next.query_id)
        .click();
      assert.equal(waiting, true);
      return done;
    },
    async exportedClaim() {
      await get("save-json").click();
      return JSON.parse(await downloads.at(-1).text()).run.claim;
    },
  };
}

function activeCase(d) {
  return d
    .get("case-list")
    .children.find((n) => n.attributes["aria-current"] === "true")?.dataset
    .case;
}

test("delayed claim selection blocks old exports and exposes only the newly loaded run", async (t) => {
  const d = await desk(t);
  assert.equal(await d.exportedClaim(), original.claim);
  const earlierMarkdown = d.get("save-md").click();
  const done = d.switchClaim();
  try {
    assert.equal(d.get("workspace").hidden, true);
    assert.equal(d.get("save-json").disabled, true);
    assert.equal(d.get("save-md").disabled, true);
    await d.get("save-json").click();
    await earlierMarkdown;
    assert.equal(d.downloads.length, 1);
  } finally {
    d.release();
    await done;
  }
  assert.equal(d.get("workspace").hidden, false);
  assert.equal(activeCase(d), next.query_id);
  assert.equal(d.get("case-context").children.at(-1).textContent, next.claim);
  assert.equal(await d.exportedClaim(), next.claim);
});

for (const [label, change] of [
  ["missing description", (record) => delete record.description],
  ["null description", (record) => (record.description = null)],
  ["wrong family ID", (record) => (record.family_id = original.family_id)],
  ["wrong technique name", (record) => (record.technique = original.technique)],
  ["missing technique URL", (record) => delete record.technique_url],
  [
    "invalid family URL",
    (record) => (record.family_url = "javascript:alert(1)"),
  ],
  ["mismatched case claim", (record) => (record.claim = original.claim)],
]) {
  test(
    "malformed fetched case preserves prior UI and export: " + label,
    async (t) => {
      const d = await desk(t, change);
      const source = d.get("source-list").querySelectorAll("button")[0];
      source.click();
      await until(() => !d.get("reset").disabled);
      const done = d.switchClaim();
      assert.equal(d.get("save-json").disabled, true);
      d.release();
      await done;
      assert.match(
        d.get("status").textContent,
        /Could not open this recorded run/,
      );
      assert.equal(activeCase(d), original.query_id);
      assert.equal(d.get("case-title").textContent, original.family);
      assert.equal(
        d.get("case-context").children.at(-1).textContent,
        original.claim,
      );
      assert.equal(d.get("workspace").hidden, false);
      const toggle = d
        .get("source-list")
        .querySelectorAll("button")
        .find((n) => n.dataset.source === source.dataset.source);
      assert.equal(toggle.textContent, "Restore");
      await d.get("save-json").click();
      const bundle = JSON.parse(await d.downloads.at(-1).text());
      assert.equal(bundle.run.claim, original.claim);
      assert.deepEqual(bundle.view.excluded_source_ids, [
        source.dataset.source,
      ]);
      assert.equal(d.get("policy").value, bundle.run.config.policy);
      assert.equal(d.get("budget").value, String(bundle.run.config.budget));
    },
  );
}

test("failed import during a claim fetch restores one coherent selectable and exportable run", async (t) => {
  const d = await desk(t),
    done = d.switchClaim();
  try {
    const file = d.get("import-file");
    file.files = [
      {
        size: 1,
        arrayBuffer: async () => new TextEncoder().encode("{").buffer,
      },
    ];
    await file.fire("change");
    assert.match(d.get("status").textContent, /Import failed/);
    assert.equal(activeCase(d), original.query_id);
    assert.equal(d.get("workspace").hidden, false);
    assert.equal(
      d.get("case-context").children.at(-1).textContent,
      original.claim,
    );
    assert.equal(await d.exportedClaim(), original.claim);
  } finally {
    d.release();
    await done;
  }
  assert.equal(activeCase(d), original.query_id);
  assert.equal(await d.exportedClaim(), original.claim);
});

test("trace rail and step navigation display the active run's recorded search, read and score", async (t) => {
  const d = await desk(t);
  const { events } = JSON.parse(
    readFileSync(new URL("data/" + original.path, site)),
  ).runs["challenge-4"];
  const paper = d.get("step-detail");
  function descendants(node) {
    return [node, ...node.children.flatMap(descendants)];
  }
  const rail = (i) =>
    d.get("steps").children.find((b) => b.dataset.step === String(i));
  const nav = (name) =>
    descendants(paper).find(
      (n) => n.tag === "button" && n.textContent === name,
    );
  async function checkStep(i, title, content) {
    const expected =
      "Recorded tool step " + events[i].sequence + " of " + events.length;
    await until(() => paper.children.some((n) => n.textContent === expected));
    assert.equal(paper.children.find((n) => n.tag === "h3").textContent, title);
    assert.ok(descendants(paper).some((n) => n.textContent === content));
  }
  rail(0).click();
  await checkStep(0, "Search the corpus", events[0].output[0].snippet);
  nav("Next step").click();
  await checkStep(1, "Read source sentences", events[1].output[0].quote);
  rail(2).click();
  await checkStep(2, "Score the excerpts", events[2].output[0].span.quote);
  nav("Previous").click();
  await checkStep(1, "Read source sentences", events[1].output[0].quote);
  rail(-1).click();
  await until(() =>
    paper
      .querySelectorAll("summary")
      .some((n) => n.textContent === "Recorded run identity"),
  );
});

for (const status of ["running", "failed"]) {
  test(
    status + " imports announce and export provisional assessments",
    async (t) => {
      const d = await desk(t);
      assert.match(d.get("audit-status").textContent, /Current verdict/);
      await d.get("save-json").click();
      const bundle = JSON.parse(await d.downloads.at(-1).text());
      const run = bundle.run;
      run.events = [];
      run.scores = [];
      run.assessment = null;
      run.status = status;
      const zero = { search: 0, read: 0, verify: 0 };
      run.usage = {
        attempts: { ...zero },
        successes: zero,
        unique_sources: 0,
        nli_pairs: 0,
      };
      if (status === "failed") {
        const { digest } = await import("../../site/decision.js");
        const event = {
          sequence: 1,
          tool: "search",
          input: { claim: run.claim, mode: "relevance", limit: 12 },
          output: null,
          attempt: 1,
          error: "permanent",
          previous_hash: null,
        };
        run.events.push({ ...event, hash: await digest(event) });
        run.usage.attempts.search = 1;
      }
      bundle.sources = {};
      const bytes = new TextEncoder().encode(JSON.stringify(bundle));
      const input = d.get("import-file");
      input.files = [
        { size: bytes.length, arrayBuffer: async () => bytes.buffer },
      ];
      await input.fire("change");
      assert.equal(activeCase(d), undefined);
      assert.equal(d.get("case-title").textContent, "Imported investigation");
      assert.doesNotMatch(d.get("evidence-note").textContent, /MITRE|malware/);
      assert.match(
        d.get("audit-status").textContent,
        new RegExp(status + ".*Provisional verdict", "i"),
      );
      await d.get("save-md").click();
      const md = await d.downloads.at(-1).text();
      assert.match(md, new RegExp("Recorded status: \\*\\*" + status));
      assert.doesNotMatch(md, /Original verdict|Current audit/);
      assert.match(md, /Provisional audit/);
      assert.match(md, /Provisional base verdict/);
    },
  );
}

test("case search matches family and technique, preserves opened case, and recovers from no matches", async (t) => {
  const d = await desk(t),
    search = d.get("case-search");
  function filter(query) {
    search.value = query;
    search.fire("input");
    return d
      .get("case-list")
      .children.filter((n) => n.dataset.case)
      .map((n) => n.dataset.case);
  }
  assert.equal(d.get("case-title").textContent, "WannaCry");
  assert.deepEqual(
    filter(" lOcKbIt "),
    index.claims
      .filter((c) => c.family.includes("LockBit"))
      .map((c) => c.query_id),
  );
  assert.ok(filter(original.technique_id).includes(original.query_id));
  assert.deepEqual(filter("no-matching-family"), []);
  assert.equal(d.get("case-count").textContent, "0 matching cases");
  assert.equal(await d.exportedClaim(), original.claim);
  assert.equal(filter("").length, 24);
});

test("evaluation navigation exposes distinct original and masked results plus policy cohorts", async (t) => {
  const d = await desk(t);
  window.location.hash = "#evaluation";
  await d.get("window").fire("hashchange");
  assert.equal(d.get("evaluation").hidden, false);
  assert.equal(d.get("investigations").hidden, true);
  const results = d.get("results");
  assert.deepEqual(
    results.querySelectorAll("h2").map((n) => n.textContent),
    [
      "ATT&CK canonical source-record Recall@5",
      "TRAM technique ranking",
      "TRAM with literal technique IDs masked",
    ],
  );
  assert.ok(
    results.querySelectorAll("td").some((n) => n.textContent === "31.805%"),
  );
  assert.ok(
    results.querySelectorAll("td").some((n) => n.textContent === "31.823%"),
  );
  assert.equal(
    d.get("agent-results").querySelectorAll("td")[0].textContent,
    64,
  );
  d.get("cohort").value = "demo";
  d.get("cohort").fire("change");
  assert.equal(
    d.get("agent-results").querySelectorAll("td")[0].textContent,
    24,
  );
  window.location.hash = "#investigations";
  await d.get("window").fire("hashchange");
  assert.equal(d.get("investigations").hidden, false);
  assert.equal(activeCase(d), original.query_id);
});

test("source withdrawal, reset and policy selection update the exported view and assessments", async (t) => {
  const d = await desk(t);
  const source = d.get("source-list").querySelectorAll("button")[0];
  source.click();
  await until(() => !d.get("reset").disabled);
  await d.get("save-json").click();
  assert.deepEqual(
    JSON.parse(await d.downloads.at(-1).text()).view.excluded_source_ids,
    [source.dataset.source],
  );
  d.get("reset").click();
  await until(() => d.get("reset").disabled);
  d.get("policy").value = "first_hit";
  await d.get("policy").fire("change");
  d.get("budget").value = "8";
  await d.get("budget").fire("change");
  await d.get("save-json").click();
  const bundle = JSON.parse(await d.downloads.at(-1).text());
  assert.deepEqual(bundle.view.excluded_source_ids, []);
  assert.equal(bundle.run.config.budget, 8);
  assert.equal(bundle.run.config.policy, "first_hit");
  await d.get("save-md").click();
  assert.match(await d.downloads.at(-1).text(), /WannaCry/);
});

test("Markdown export attributes verified ATT&CK records and keeps unrelated imports generic", async (t) => {
  const d = await desk(t);
  const license = readFileSync(new URL("assets/licenses/attack-license.txt", site), "utf8");
  async function exportMarkdown() {
    await d.get("save-md").click();
    return d.downloads.at(-1).text();
  }
  async function importBundle(bundle) {
    const file = d.get("import-file");
    const bytes = new TextEncoder().encode(JSON.stringify(bundle));
    file.files = [{ size: bytes.length, arrayBuffer: async () => bytes.buffer }];
    await file.fire("change");
    assert.match(d.get("status").textContent, /Imported run checked/);
  }
  const first = await exportMarkdown();
  assert.match(first, /quote MITRE ATT&CK® procedure summaries/);
  assert.match(first, /Vendor report text was never retrieved/);
  assert.match(first, /\[Canonical reference\]\(</);
  assert.match(first, /refutation therefore requires independent factual verification/);
  assert.ok(first.includes(license));

  await d.get("save-json").click();
  const cyber = JSON.parse(await d.downloads.at(-1).text());
  await importBundle(cyber);
  assert.equal(await exportMarkdown(), first);

  const legacy = new URL("../archive/scifact-demo/", site);
  const { claims } = JSON.parse(readFileSync(new URL("index.json", legacy)));
  const sample = JSON.parse(readFileSync(new URL(claims[0].path, legacy)));
  const { bundleFor } = await import("../../site/import.js");
  const other = bundleFor(sample.runs["challenge-4"], sample.sources, []);
  for (const corpusDigest of [other.run.corpus_digest, cyber.run.corpus_digest]) {
    other.run.corpus_digest = corpusDigest;
    await importBundle(other);
    const report = await exportMarkdown();
    assert.match(report, /## Recorded evidence/);
    assert.doesNotMatch(report, /MITRE|Canonical reference/);
  }
});
