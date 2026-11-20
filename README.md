# AgentResearch

AgentResearch investigates claims about malware behavior using published threat intelligence. Open a saved case, read the exact MITRE ATT&CK® wording the model scored, and withdraw a source record to see whether the assessment changes. The browser starts with WannaCry and includes 23 other cases, including LockBit, BlackCat and Conti. [Open the case browser](https://sashankpotluru.github.io/AgentResearch/) · [Download the case index](site/data/index.json)

## What the numbers show

On **10,739 ATT&CK family–technique queries**, word BM25 achieved **97.625% mean canonical source-record Recall@5**. Filtering by known malware-family membership raised it to **99.208%**. Each gold record groups MITRE summaries under a selected reference URL. Duplicate summaries pool references across relationships, so a gold URL can differ from the one cited by the queried relationship. The filter uses extra catalogue metadata. [Protocol](experiments/cyber-protocol.json) · [Measured results](results-cyber/summary.json)

Matching report sentences to technique definitions was harder. On **4,036 unique TRAM sentences from 149 reports**, character TF-IDF led both word BM25 and reciprocal rank fusion (RRF). After reviewing the original results, we measured how much literal technique IDs in the input affected ranking. The diagnostic uses the same sentences, labels, candidate definitions and parameters. [Original rankings](results-cyber/tram-rankings.json.gz) · [Post-hoc diagnostic protocol](experiments/cyber-diagnostic-protocol.json) · [Masked rankings](results-cyber/tram-id-masked-rankings.json.gz)

| Mean Recall@5 over 4,036 labeled sentences | Word BM25 | Character TF-IDF | RRF |
| --- | ---: | ---: | ---: |
| Original sentences | 22.992% | **31.805%** | 29.047% |
| Post-hoc diagnostic with literal IDs masked | 22.992% | **31.823%** | 29.022% |

**The neural model transferred poorly.** Its 0.90 threshold came from science claims. At an eight-source budget, fixed acquisition supported **0 of 64** positive-derived malware claims, refuted 45 and returned conflict on 19. First hit supported 7 of 64 using 1.48 reads per case. These counts measure model behavior on positives. They cannot estimate balanced factual accuracy. The 24 illustrative browser cases have a separate sample and results. [Model outcomes, costs and intervals](docs/evaluation.md) · [Limitations](docs/limitations.md)

## Inspect a case

1. Select a family or search by technique ID. Choose a policy and a four- or eight-source budget to open its saved run.
2. Read the scored excerpts and their locations. Each source groups MITRE procedure summaries by canonical reference URL. The investigator scored MITRE’s wording. The links identify the selected references.
3. Withdraw a source record to remove all its scores. A change measures source dependence. Publications can share the same underlying observation. Restore the source to compare with the original assessment.
4. Expand **Run trace** to inspect the saved searches, reads and scores. Export the current view as portable JSON or Markdown. Import a portable bundle up to 5 MiB to replay it locally. [Investigation format](docs/format.md)

## Run it locally

From a checkout with Python 3.12 or later, verify the published release without downloading model weights or running inference. To inspect the browser locally, open `http://127.0.0.1:8000` after starting the server.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -c requirements-lock.txt .
python scripts/cyber.py verify --results results-cyber --site-dir site/data
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

To investigate a new claim, install the model extra and acquire the seven pinned model files. The standard Python investigator, CLI and MCP interface accept the published malware corpus. [Model setup](docs/model.md) · [Local runs, audit and bundle examples](docs/reproduce.md) · [Checkpoint resume and MCP](docs/integrations.md)

```sh
agentresearch run --claim "WannaCry uses Peripheral Device Discovery." \
  --corpus results-cyber/corpus.jsonl --model-dir .artifacts/model \
  --policy challenge --budget 4 --threshold 900000 \
  --db .artifacts/runs.sqlite --cache .artifacts/scores.sqlite > .artifacts/run.json
agentresearch audit .artifacts/run.json results-cyber/corpus.jsonl
agentresearch report .artifacts/run.json results-cyber/corpus.jsonl --bundle > .artifacts/bundle.json
```

## How the pieces fit

![AgentResearch architecture showing the investigator and source-withdrawal replay alongside separate ATT&CK and TRAM ranking benchmarks](site/assets/architecture.svg?v=cyber-v1)

The Python investigator reads a fixed corpus, checkpoints tool calls in SQLite, stores exact excerpts and scores, then replays one-source withdrawals. The Pages browser reads static bundles and calculates the effect of exclusions from saved scores. The offline verifier replays retrieval and all 704 saved policy runs against the prepared release. [Method](docs/method.md) · [Artifact manifest](results-cyber/manifest.json) · [Reproduction instructions](docs/reproduce.md)

## Sources and earlier work

MITRE ATT&CK supplies 747 malware families, 10,695 distinct procedure summaries and 1,120 source records grouped by reference URL. TRAM supplies labeled sentences for the separate technique-ranking benchmark. Pinned source revisions, preparation steps and full reuse terms accompany the release. [Data construction](docs/datasets.md) · [Source pins](data/cyber-sources.json) · [Third-party notices](THIRD_PARTY_NOTICES.md)

The original SciFact and Climate-FEVER study remains available with its frozen calibration, traces and browser payloads. [Archived evaluation](docs/legacy-evaluation.md) · [Science results](results/) · [Archived SciFact cases](archive/scifact-demo/)

Original software is MIT licensed. [License](LICENSE) · [Citation](CITATION.cff)
