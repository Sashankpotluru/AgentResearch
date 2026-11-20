# Investigation method

A claim enters as a string and the investigator receives a fixed source corpus. In the malware release, a source groups cleaned MITRE procedure summaries under one cited publication. Researchers can also supply their own records. The agent can search the corpus, read source sentences and request natural language inference (NLI) on the retained excerpts. Evaluation annotations are stored separately. [Corpus format](format.md) · [Data preparation](datasets.md)

![AgentResearch investigation and evaluation architecture](../site/assets/architecture.svg?v=cyber-v1)

## Acquisition

BM25 ranks sources using their title twice and body once. A read selects up to two sentences. Fixed reads down the relevance ranking. First hit stops on the first base verdict other than INSUFFICIENT. Challenge reads the first three sources, or exhausts the initial ranking, then searches again if budget remains. An insufficient provisional verdict triggers a search on the four highest-IDF claim terms. A substantive verdict triggers a lexical boost for contrast cues such as “however” and “not.” Guarded uses the same acquisition as challenge. Each successful read consumes one source from the budget. [Frozen retrieval and policy parameters](../experiments/cyber-protocol.json)

The locked cross-encoder assesses the sentence as premise and the full claim as hypothesis. The trace preserves the prefix that fit the 512-token model input, its Unicode character offsets and three integer scores summing to one million. Threshold selection used fixed/eight on 537 SciFact training claims and chose 900,000 millionths. The cybersecurity experiment transferred that threshold unchanged, and the model frequently labeled unrelated behavior as a refutation. [Verifier and score bindings](model.md) · [Frozen calibration](../experiments/calibration.json)

## Decisions and withdrawal

The strongest support and refutation judgments above or at threshold compete at the source level. Support and refutation from distinct sources yield CONFLICT. Opposing judgments confined to one source yield INSUFFICIENT with an internal-conflict flag. A single active side yields SUPPORTS or REFUTES. With no active side the verdict is INSUFFICIENT. Ties resolve by score and stable source/span identity. [Decision implementation](../src/agentresearch/decision.py)

Every completed run removes each scored source in turn, drops all of its excerpts, and recomputes the base verdict. A source is pivotal if that verdict changes. Guarded emits ABSTAIN when any single removal is pivotal. Retain the base verdict and its witnesses when interpreting an abstention. Manually withdrawing several sources in the desk computes the same rule on the remaining scores. The export carries the original run and the current exclusion list. [Browser decision code](../site/decision.js) · [Report format](format.md)

## What a replay establishes

The CLI checks a run against the supplied full corpus. It replays the policy, search rankings, sentence selections, event hashes, integer judgments, source withdrawals and final report. Scoring a new claim requires the model. Replaying an existing claim requires its stored scores. SQLite transactions commit a tool outcome with its checkpoint. A crash before commit can repeat a physical call on resume. [CLI and MCP guide](integrations.md)

Portable bundles include every source returned by a successful search or referenced by a read. The browser checks the sequence, snippet and quote bindings, event hashes, recorded model identity, costs and derived decisions. The subset cannot authenticate the original corpus digest, reconstruct a ranking against all unreturned candidates, or prove how the recorded scores were obtained. Inspect the benchmark's paired comparisons and failure counts before interpreting the example runs. [Evaluation definitions](evaluation.md) · [Limitations](limitations.md)
