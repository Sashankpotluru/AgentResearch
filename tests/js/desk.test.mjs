import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  audit,
  canonical,
  decide,
  digest,
  viewAssessment,
} from "../../site/decision.js";
import {
  parseBundle,
  validateImport,
  bundleFor,
  markdown,
  safeUrl,
} from "../../site/import.js";

const data = new URL("../../archive/scifact-demo/", import.meta.url);
const index = JSON.parse(readFileSync(new URL("index.json", data)));
const record = JSON.parse(readFileSync(new URL(index.claims[0].path, data)));
const example = () => bundleFor(record.runs["challenge-4"], record.sources, []);
const clone = structuredClone;

test("all eight published Python traces match JS witnesses, audits, guard and event hashes", async () => {
  for (const run of Object.values(record.runs)) {
    const bundle = bundleFor(run, record.sources, []);
    assert.deepEqual(await viewAssessment(run, []), run.assessment);
    assert.deepEqual(
      await decide(run.scores, run.config.threshold),
      run.assessment.base,
    );
    assert.deepEqual(await validateImport(bundle), bundle);
  }
});

test("withdrawing a pivotal source removes all its scores and reset restores the complete audit", async () => {
  const { run } = example();
  const sid = run.assessment.pivotal_sources[0];
  assert.ok(run.scores.filter((row) => row.span.source_id === sid).length > 1);
  assert.deepEqual(
    (await viewAssessment(run, [sid])).base,
    run.assessment.withdrawals[sid],
  );
  const none = await viewAssessment(
    run,
    Object.keys(run.assessment.withdrawals),
  );
  assert.equal(none.final.verdict, "INSUFFICIENT");
  assert.deepEqual(none.pivotal_sources, []);
  assert.deepEqual(await viewAssessment(run, []), run.assessment);
  const b = bundleFor(run, record.sources, [sid]);
  assert.deepEqual(
    (await validateImport(parseBundle(JSON.stringify(b)))).view
      .excluded_source_ids,
    [sid],
  );
  assert.deepEqual(b.run, run);
});

test("conflicting polarities within one source and Unicode tie-breaking", async () => {
  const first = clone(example().run.scores[0]);
  const other = clone(first);
  first.span.source_id = other.span.source_id = "𐀀";
  first.entailment = other.contradiction = 950000;
  first.contradiction = other.entailment = 0;
  first.neutral = other.neutral = 50000;
  other.span.sentence_id += "2";
  const same = await decide([first, other], 950000);
  assert.equal(same.internal_conflict, true);
  assert.equal(same.verdict, "INSUFFICIENT");
  const separate = clone(first);
  separate.span.source_id = "\uE000";
  const three = await audit([other, first, separate], 950000);
  assert.equal(three.base.verdict, "CONFLICT");
  assert.deepEqual(three.base.witnesses, [
    await digest(separate.span),
    await digest(other.span),
  ]);
  assert.deepEqual(three.pivotal_sources, ["\uE000", "𐀀"]);
  await assert.rejects(() => decide([first, first], 0));
});

test("canonical ordering uses Unicode code points, strict portable JSON and the byte limit", () => {
  assert.equal(
    canonical({ "𐀀": 1, "\uE000": 2, 10: 3, 2: 4 }),
    '{"10":3,"2":4,"\uE000":2,"𐀀":1}',
  );
  for (const content of [
    '{"q":1,"\\u0071":2}',
    '{"a":{"b":1,"b":2}}',
    '{"q":9007199254740992}',
    '{"q":1.0}',
    '{"q":1e0}',
    '{"q":"\\udfff"}',
    '{"q":NaN}',
    "{}{}",
    '{"q":1,}',
  ]) {
    assert.throws(() => parseBundle(content), content);
  }
  assert.throws(() => parseBundle('"' + "🧊".repeat(1310720) + '"'));
  assert.throws(() => canonical({ bad: "\uD800" }));
  assert.deepEqual(
    JSON.parse(canonical(parseBundle('{"__proto__":"x"}'))),
    JSON.parse('{"__proto__":"x"}'),
  );
});

test("portable replay rejects source, model, quote, chain, extra field, summary and view tampering", async () => {
  const mutate = [
    (b) => {
      delete b.sources[b.run.events[0].output.at(-1).source_id];
    },
    (b) => {
      b.run.events[0].hash = "a".repeat(64);
    },
    (b) => {
      b.run.scores[0].model_digest = "other";
    },
    (b) => {
      b.run.events[1].output[0].quote += "x";
    },
    (b) => {
      b.sources[b.run.scores[0].span.source_id].text += "🧊";
    },
    (b) => {
      b.run.usage.unique_sources = true;
    },
    (b) => {
      b.run.unknown = "field";
    },
    (b) => {
      b.run.assessment.base.verdict = "wrong";
    },
    (b) => {
      b.view.excluded_source_ids = ["missing"];
    },
    (b) => {
      b.view.excluded_source_ids = Array(2).fill(
        b.run.scores[0].span.source_id,
      );
    },
    (b) => {
      b.run.config.budget = Number.MAX_SAFE_INTEGER + 1;
    },
  ];
  for (const change of mutate) {
    const b = clone(example());
    change(b);
    await assert.rejects(() => validateImport(b));
  }
});

test("safe links and literal Markdown evidence distinguish original from withdrawn", async () => {
  const bundle = clone(example());
  const sid = bundle.run.scores[0].span.source_id;
  bundle.view.excluded_source_ids.push(sid);
  bundle.sources[sid].title = "<img onerror=x> [inject](javascript:evil)";
  bundle.sources[sid].url = "https://pubmed.ncbi.nlm.nih.gov/1/";
  const output = await markdown(bundle);
  assert.match(output, /Original verdict/);
  assert.match(output, /Current audit/);
  assert.match(output, /https:\/\/pubmed.ncbi.nlm.nih.gov\/1\//);
  assert.doesNotMatch(output, /<img|\[inject\]\(javascript:/);
  for (const link of [
    "javascript:evil",
    "https://x:y@example.org",
    "https://example.org/\nfoo",
    "//example.org",
    "https://example.org/>)\n# inject",
    "https://example.org/\\bad",
  ]) {
    assert.equal(safeUrl(link), null);
  }
});

for (const [name, root, count] of [
  ["archived SciFact", data, 40],
  ["malware", new URL("../../site/data/", import.meta.url), 24],
]) {
  test(
    "all " + name + " demo runs replay across Python and the browser",
    async () => {
      const cases = JSON.parse(
        readFileSync(new URL("index.json", root)),
      ).claims;
      assert.equal(cases.length, count);
      let checked = 0;
      for (const item of cases) {
        const group = JSON.parse(readFileSync(new URL(item.path, root)));
        assert.equal(Object.keys(group.runs).length, 8);
        for (const run of Object.values(group.runs)) {
          await validateImport(bundleFor(run, group.sources, []));
          checked++;
        }
      }
      assert.equal(checked, count * 8);
    },
  );
}

async function rechain(events) {
  let previous = null;
  for (const e of events) {
    e.previous_hash = previous;
    const { hash, ...body } = e;
    e.hash = await digest(body);
    previous = e.hash;
  }
}

test("rehashed malformed excerpts, snippets, model identities and scheduled inputs fail independently of checksums", async () => {
  const changes = [
    (b) => {
      b.run.events[0].output[0].snippet += "different";
    },
    (b) => {
      b.run.events[1].output[0].quote += "x";
    },
    (b) => {
      b.run.events[2].output[0].span.start++;
    },
    (b) => {
      b.run.events[2].output[0].model_digest = "other";
    },
    (b) => {
      b.run.events[1].input.source_id = b.run.events[0].output[1].source_id;
    },
    (b) => {
      b.run.events[2].output[0].neutral++;
    },
  ];
  for (const change of changes) {
    const b = clone(example());
    change(b);
    await rechain(b.run.events);
    await assert.rejects(
      () => validateImport(b),
      (err) => !/Event hash mismatch/.test(err.message),
    );
  }
});

test("portable Unicode offsets preserve astral text, truncated scores and hostile-looking source identifiers", async () => {
  const source = {
    source_id: "__proto__",
    title: "<img onerror=alert(1)>",
    text: "🧊 ice.\nA β.",
    sentences: [
      { sentence_id: "a", start: 0, end: 6 },
      { sentence_id: "b", start: 7, end: 11 },
    ],
    url: "https://pubmed.ncbi.nlm.nih.gov/123/",
  };
  const source_digest = await digest(source);
  assert.equal(
    source_digest,
    "f8ceb4752cea8909f32aabd3668b8228eb3a787d0a952b4e8caa8152ee6530ae",
  );
  const spans = source.sentences.map((s) => ({
    ...s,
    source_id: source.source_id,
    source_digest,
    quote: Array.from(source.text).slice(s.start, s.end).join(""),
  }));
  const rows = spans.map((span, i) => ({
    span: i ? span : { ...span, end: 3, quote: "🧊 i" },
    contradiction: 0,
    entailment: i ? 0 : 1000000,
    neutral: i ? 1000000 : 0,
    model_digest: "fixture",
  }));
  assert.equal(
    await digest(rows[0].span),
    "14ab0b655a2343c495395b2725b1662c8dfac8fe99472178d5e81b3d28bc85ed",
  );
  const claim = "Ice <script>unsafe()</script>";
  const events = [
    {
      tool: "search",
      input: { claim, mode: "relevance", limit: 12 },
      output: [
        { source_id: source.source_id, score: 17, snippet: source.text },
      ],
    },
    {
      tool: "read",
      input: { claim, source_id: source.source_id },
      output: spans,
    },
    { tool: "verify", input: { claim, spans }, output: rows },
  ].map((e, i) => ({ ...e, sequence: i + 1, attempt: 1, error: null }));
  await rechain(events);
  const calls = { search: 1, read: 1, verify: 1 };
  const run = {
    schema_version: 1,
    run_id: "sample",
    claim,
    config: {
      policy: "guarded",
      budget: 1,
      threshold: 900000,
      model_digest: "fixture",
    },
    corpus_digest: await digest({ schema_version: 1, sources: [source] }),
    model_digest: "fixture",
    status: "complete",
    events,
    scores: rows,
    usage: {
      attempts: calls,
      successes: calls,
      unique_sources: 1,
      nli_pairs: 2,
    },
  };
  run.assessment = await viewAssessment(run, []);
  const b = bundleFor(run, { [source.source_id]: source }, ["__proto__"]);
  await validateImport(parseBundle(JSON.stringify(b)));
  const md = await markdown(b);
  assert.ok(md.includes("🧊 i"));
  assert.doesNotMatch(md, /<script>|<img/);
  assert.equal(run.assessment.final.verdict, "ABSTAIN");
  assert.equal(run.assessment.withdrawals["__proto__"].verdict, "INSUFFICIENT");
});

test("Markdown preserves interior whitespace in identifiers, titles and the claim", async () => {
  const bundle = clone(example());
  const sid = bundle.run.scores[0].span.source_id;
  bundle.sources[sid].title = "Lab  A\r\nStudy";
  bundle.run.claim = "Claim  with   gaps\nand a newline";
  bundle.view.excluded_source_ids = ["lab  A", "lab A"];
  const output = await markdown(bundle);
  assert.match(output, /Lab  A Study/);
  assert.match(output, /Claim  with   gaps and a newline/);
  assert.match(output, /Excluded sources: lab  A, lab A/);
});
