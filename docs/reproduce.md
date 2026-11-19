# Reproduce a result or investigate a new claim

## Replay the published benchmark

Use Python 3.12 or later, starting in a checkout. The verifier uses the committed results, the full public SciFact corpus and the separately frozen calibration. It recomputes paired intervals from the numeric rows and replays the SciFact investigations. This path requires the base package.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -c requirements-lock.txt '.[test]'
python -m pytest -q
python scripts/verify_release.py --results results --site-dir site/data \
  --frozen-calibration experiments/calibration.json
```

To check Climate lexical rankings and source bindings, obtain the authors' release through the pinned downloader. Supply the prepared files to the same independent verifier.

```sh
agentresearch prepare --download --raw-dir .artifacts/raw --out-dir .artifacts/prepared
python scripts/verify_release.py --results results --site-dir site/data \
  --prepared .artifacts/prepared --frozen-calibration experiments/calibration.json
```

To repeat neural scoring from scratch, follow the pinned model setup and the calibration and held-out commands. Use a separate checkout or destination for a new experiment. The published run used float32 MPS inference with a batch size of 16. Hardware, batch shapes and library versions can affect fresh model output. Exact decision replay uses the published integer scores. [Model setup](model.md) · [Full evaluation commands](evaluation.md) · [Physical costs](../results/physical.json)

## Investigate a new claim

Install the model extra and download the seven files in the model lock using the instructions above. Choose an approved corpus in JSON or prepared JSONL format. This example uses the public SciFact corpus already in the checkout.

```sh
python -m pip install -c requirements-lock.txt '.[model]'
mkdir -p .artifacts
agentresearch run --claim "Leaf exposure affects photosynthesis." \
  --corpus results/scifact/corpus.jsonl --model-dir .artifacts/model \
  --policy challenge --budget 4 --threshold 900000 \
  --db .artifacts/runs.sqlite --cache .artifacts/scores.sqlite \
  --run-id leaf-exposure > .artifacts/run.json

agentresearch audit .artifacts/run.json results/scifact/corpus.jsonl
agentresearch report .artifacts/run.json results/scifact/corpus.jsonl > .artifacts/report.md
agentresearch report .artifacts/run.json results/scifact/corpus.jsonl --bundle > .artifacts/bundle.json
```

Open the desk and choose **Import run** to inspect the bundle. **Export → Portable JSON** includes the original trace and the sources currently withdrawn. **Export → Markdown report** gives the recorded verdict, current audit, cited excerpts and score triples. The browser's 5 MiB import limit includes the complete source texts referenced by search hits. For larger source collections, audit the original run and corpus locally. Local `audit` and `report` read run files up to 64 MiB by default. Set a positive byte limit with `--max-run-bytes` for a larger trace or a tighter memory budget. Portable bundles retain the 5 MiB limit. [Research desk](https://sashankpotluru.github.io/AgentResearch/)

For a bounded search of fresh PubMed abstracts, use the connector and pass the resulting JSON as the corpus. To call the same investigator from an MCP host, install the model and MCP extras and use stdio. [PubMed, checkpoint resume and MCP examples](integrations.md)

## Test and serve the desk

```sh
python -m pip install -c requirements-lock.txt '.[test,mcp]'
python -m pytest -q
npm test
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open localhost on port 8000. The JavaScript tests use Node 22 or later with its built-in test runner. CI tests Python 3.12 and 3.13, including the official MCP stdio round trip, checks browser/Python parity, then replays the public benchmark before deploying main. The optional model smoke was run locally on MPS with the verified weight files. Run a new claim with the locked local weights to exercise inference on your machine. [Verification workflow](../.github/workflows/ci.yml)
