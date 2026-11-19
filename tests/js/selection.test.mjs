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
  setAttribute() {}
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

async function desk(t) {
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
  const document = {
    getElementById: get,
    createElement: (tag) => new Element(tag, downloads),
    body: new Element("body", downloads),
  };
  let release,
    waiting = false;
  const delayed = new Promise((resolve) => {
    release = resolve;
  });
  const testGlobals = {
    document,
    Option: class extends Element {
      constructor(label, value) {
        super("option", downloads);
        this.textContent = label;
        this.value = value;
      }
    },
    fetch: async (path) => {
      if (path === "data/" + next.path) {
        waiting = true;
        await delayed;
      }
      if (path === "data/summary.json") return { ok: false, status: 404 };
      return {
        ok: true,
        json: async () => JSON.parse(readFileSync(new URL(path, site))),
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
      get("claim").value = next.query_id;
      const done = get("claim").fire("change");
      assert.equal(waiting, true);
      return done;
    },
    async exportedClaim() {
      await get("save-json").click();
      return JSON.parse(await downloads.at(-1).text()).run.claim;
    },
  };
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
  assert.equal(d.get("claim").value, next.query_id);
  assert.equal(
    d.get("paper").children.find((n) => n.tag === "h3").textContent,
    next.claim,
  );
  assert.equal(await d.exportedClaim(), next.claim);
});

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
    assert.equal(d.get("claim").value, original.query_id);
    assert.equal(d.get("workspace").hidden, false);
    assert.equal(
      d.get("paper").children.find((n) => n.tag === "h3").textContent,
      original.claim,
    );
    assert.equal(await d.exportedClaim(), original.claim);
  } finally {
    d.release();
    await done;
  }
  assert.equal(d.get("claim").value, original.query_id);
  assert.equal(await d.exportedClaim(), original.claim);
});

test("trace rail and step navigation display the active run's recorded search, read and score", async (t) => {
  const d = await desk(t);
  const { events } = JSON.parse(
    readFileSync(new URL("data/" + original.path, site)),
  ).runs["challenge-4"];
  const paper = d.get("paper");
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
    paper.children.some(
      (n) => n.tag === "h3" && n.textContent === original.claim,
    ),
  );
});
