# Reproduce and inspect the malware release

## Offline replay

From a checkout, install Python 3.12 or later and the package. The released prepared data, rankings, 704 canonical model runs, and 24 browser cases are sufficient for verification. The offline verifier replays saved scores without loading model weights.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -c requirements-lock.txt .
python scripts/cyber.py verify --results results-cyber --site-dir site/data
```

## Rebuild the inputs or repeat inference

The following commands use fresh output paths under `.artifacts/reproduction`. The downloader checks pinned input sizes and SHA-256. Strict verification derives the prepared corpus from these original downloads before replaying the published outputs.

```sh
python scripts/cyber.py prepare --download \
  --raw .artifacts/reproduction/raw --prepared .artifacts/reproduction/prepared
python scripts/cyber.py verify --results results-cyber --site-dir site/data \
  --strict-intake .artifacts/reproduction/raw
```

To repeat scoring, install the model extra and download the pinned weights as documented in the model guide. The original cyber run used float32 on MPS at batch size eight. CPU and CUDA are also supported. Fresh scores can vary by runtime and device. [Model setup](model.md) · [Published runtime identity](../results-cyber/model.json)

```sh
python scripts/cyber.py evaluate --prepared .artifacts/reproduction/prepared \
  --work .artifacts/reproduction/runs --model .artifacts/model --device mps --batch-size 8 \
  --results .artifacts/reproduction/results --site-dir .artifacts/reproduction/demo
python scripts/cyber.py verify --results .artifacts/reproduction/results \
  --site-dir .artifacts/reproduction/demo
```

Once measured records exist, `python scripts/cyber.py package` accepts the same prepared, work, results and site directories to export the records again without rescoring.

## Investigate and export one claim

With the pinned model installed, use the published malware corpus in the standard run, audit and report commands. Policy and budget are explicit. The threshold is expressed in integer millionths.

```sh
mkdir -p .artifacts
agentresearch run --claim "WannaCry uses Peripheral Device Discovery." \
  --corpus results-cyber/corpus.jsonl --model-dir .artifacts/model \
  --policy challenge --budget 4 --threshold 900000 --run-id wannacry-peripheral \
  --db .artifacts/runs.sqlite --cache .artifacts/scores.sqlite > .artifacts/run.json
agentresearch audit .artifacts/run.json results-cyber/corpus.jsonl
agentresearch report .artifacts/run.json results-cyber/corpus.jsonl > .artifacts/report.md
agentresearch report .artifacts/run.json results-cyber/corpus.jsonl --bundle > .artifacts/bundle.json
```

Open **Import run** in Investigations and select the portable bundle. **Export → Portable JSON** includes the original run, its supplied source records and the current exclusion list. **Markdown report** includes the current audit and its evidence. Browser bundles have a 5 MiB limit, including search-hit source texts. Local audit and report have a default 64 MiB run-file limit that can be adjusted with `--max-run-bytes`. [Browser](https://sashankpotluru.github.io/AgentResearch/) · [Resume and MCP examples](integrations.md)

## Test and serve

```sh
python -m pip install -c requirements-lock.txt '.[test,mcp]'
python -m pytest -q
npm test
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8000`. The JavaScript suite uses Node 22 or later. CI runs the Python and browser suites and verifies both releases before deploying Pages. The archived study uses its own verifier and browser directory. [CI workflow](../.github/workflows/ci.yml) · [Archived science evaluation](legacy-evaluation.md)

```sh
python scripts/verify_release.py --results results --site-dir archive/scifact-demo \
  --frozen-calibration experiments/calibration.json
```
