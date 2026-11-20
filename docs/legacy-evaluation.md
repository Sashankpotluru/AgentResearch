# Archived science study · Reproducing the frozen evaluation

The versioned protocol lives in `experiments/protocol.json`. It fixes datasets, splits, eligible labels, policies, budgets, threshold selection, comparisons and uncertainty estimates. See [dataset provenance](legacy-datasets.md), [model setup](model.md) and the [investigation format](format.md) for the input contracts. The release runs use integer model judgments and a deterministic tool policy.

## Running the experiment

From a checkout with the package's model dependencies and pinned local weights, run the following commands. The example uses MPS and model batches of 16. The model identity includes these settings and library versions. Use the same identity for both stages.

```sh
python scripts/reproduce.py prepare --download --raw-dir .artifacts/raw --out-dir .artifacts/prepared
python scripts/reproduce.py calibrate --prepared .artifacts/prepared --model .artifacts/model \
  --cache .artifacts/scores.sqlite --device mps --batch-size 16 \
  --output experiments/calibration.json
```

Independently check the selection against the prepared calibration split, then commit it before evaluating held-out claims.

```sh
PYTHONPATH=src python - <<'PYTHON'
from agentresearch.experiment import protocol_path, read
from agentresearch.verify import check_calibration

check_calibration(read("experiments/calibration.json"), read(protocol_path()),
                  prepared_dir=".artifacts/prepared")
PYTHON
```

The calibration stage runs fixed acquisition once per claim at eight reads. It applies each grid threshold to the resulting identical score ledger, computes three-class macro F1, and breaks exact rational ties by taking the higher threshold. The artifact includes every calibration claim's numeric scores, outcomes, benchmark class, model identity, and split and protocol hashes. The command prints its byte digest and physical invocation measurements.

```sh
python scripts/reproduce.py evaluate --prepared .artifacts/prepared --model .artifacts/model \
  --cache .artifacts/scores.sqlite --device mps --batch-size 16 \
  --calibration experiments/calibration.json --output results \
  --work-dir .artifacts/eval-work --site-dir archive/scifact-demo
python scripts/verify_release.py --results results --site-dir archive/scifact-demo
python scripts/verify_release.py --results results --site-dir archive/scifact-demo --prepared .artifacts/prepared \
  --frozen-calibration experiments/calibration.json
```

The installed commands are `agentresearch calibrate`, `agentresearch evaluate`, and `agentresearch verify --results results [--site-dir archive/scifact-demo] [--prepared .artifacts/prepared]`. The Python entry points `experiment.calibrate(prepared, model, cache, output, *, device, batch_size, warm)` and `experiment.evaluate(prepared, model, cache, calibration, output, *, device, batch_size, work_dir, site_dir, warm)` accept the same paths. `verify.verify_results(path, prepared_dir=None, *, site_dir=None, frozen_calibration=None, frozen_sha256=None)` raises on mismatches.

The release verifier checks the calibration copy against the separately frozen selection. From a source checkout it defaults to `experiments/calibration.json`. For a custom reproduction, pass `--frozen-calibration PATH` or a previously recorded `--frozen-sha256 HEX`. Prepared verification checks the original fixed-eight source ranking, ordered sentence selection, and valid retained prefix lengths for every calibration query.

Resume an interrupted evaluation by repeating the evaluation command with the same paths. Each completed query's eight runs form one checksummed, atomically replaced checkpoint. The checkpoint directory depends on the calibration file hash, dataset and corpus digest. Checkpoint loading validates the query identity, all eight configurations, and SciFact trace completion before accumulating records. Interrupted claims restart. Public writes stage in a private directory on the destination filesystem before atomic replacement. Invalid or incomplete runs stop the pipeline. Finish verification before publishing the release.

## Measurement

Each dataset uses its frozen set of benchmark classes. SciFact has three and Climate-FEVER has four. Macro F1 averages class-specific F1 values with zero for an undefined class. An additional predicted class counts against the gold class. Accuracy, per-class recall and a confusion matrix accompany macro F1. Coverage counts every final verdict except ABSTAIN, including INSUFFICIENT. Risk is the error fraction among covered claims, with null when no claims are covered. The pivotal rate counts queries whose base verdict changes after at least one single-source withdrawal.

Sentence recall divides the number of retained positive annotated sentences by their total, summed over queries. A score receives credit only when its exact retained prefix covers the complete non-whitespace sentence text. The complete-rationale rate counts eligible queries for which at least one full positive rationale survives. Climate positive candidates are singleton rationale groups. Quote integrity measures exact source-slice binding. An exact quote can support an incorrect model judgment, and an unannotated sentence can contain relevant evidence.

Read the primary contrast as challenge minus fixed at budget eight. Guarded minus challenge measures selective risk and coverage. First-hit minus fixed measures cost alongside classification quality. The four-read configurations provide sensitivity comparisons.

Within each dataset, 2,000 bootstrap draws sample connected components with replacement using seed 17. Every sampled component contributes all its claims with multiplicity. Each draw uses the same resample across every policy and budget before taking paired differences. Percentile intervals use linear interpolation at 2.5% and 97.5%. Intervals include the number of defined draws, including when a risk denominator vanishes. The fixed macro F1 class set applies to every resample. Climate-FEVER also includes paired claim-level resampling. Its largest evidence-sharing component contains 1,040 of 1,535 claims. This concentration limits interpretation of the component intervals. Both clustering schemes can miss correlations across sources.

The logical cost counts attempts at search, read and verify, together with the number of scored sentence pairs in the completed trace. Physical scoring is shared across policies and budgets. For each consecutive block of up to 16 claims, prefetch scores the relevance top eight, uses each claim's first three source readings to choose focus or challenge at the frozen threshold, then scores only the extra sources an eight-read challenge run would reach. Each model batch contains up to the configured batch size. At most 13 distinct sources can enter this union for one claim. The logical investigator still calls, records, and validates each scheduled tool. Prefetch receives claim text, the corpus index and the verifier. Annotations enter only the evaluation join. `--no-prefetch` uses on-demand scoring and can change physical batching. Hardware or batching changes can affect floating-point inference before quantization.

Physical timings in `physical.json` cover instrumented scoring and trace-construction stages for each dataset. `invocation` describes the latest evaluation process. `sessions` retains observations from earlier invocations in the same work directory. Previously warmed caches shift costs to the warming process. Record that process separately. A hard stop can leave physical work since the last flushed observation uncounted. `cache_reused_pairs` counts requested pairs resolved without new inference, including repeated requests within a batch.

## Result artifacts

All paths below are relative to `results/` unless stated otherwise.

| Artifact | Contents |
|---|---|
| `manifest.json` | SHA-256 of each result file and site data file, plus the frozen protocol and calibration digests |
| `protocol.json`, `calibration.json` | Exact copies of the preregistered protocol and selected-threshold input |
| `summary.json`, `summary.csv` | Full statistics, paired contrasts and intervals in JSON, and point estimates per configuration in CSV |
| `scifact/records.json.gz`, `climate/records.json.gz` | Versioned numeric ledger with one row per query, policy and budget |
| `scifact/corpus.jsonl`, `scifact/dev.jsonl` | Full corpus and original held-out annotations in the pinned prepared format |
| `scifact/traces/<query_id>.json.gz` | All eight complete, replayable investigation traces for that SciFact query |
| `physical.json` | Device, batch size, elapsed seconds, physical, truncated, requested and cache-reused pair counts |

The public Climate rows contain the original numeric query ID string, a numeric cluster ID string, derived numeric benchmark class and count observations. They use opaque integer source and sentence ordinals. The release supplies no Climate claim, article title, original annotation links, evidence text, or quoted trace. The prepared-data verification command reacquires those checks locally after dataset preparation.

In each `records` array, class indices 0–4 denote SUPPORTS, REFUTES, INSUFFICIENT, CONFLICT and ABSTAIN. A record's `label`, `prediction` and `base` use these indices. Each score is `[source_ordinal, sentence_ordinal, retained_codepoint_length, contradiction, entailment, neutral]`. Ordinals start at zero in the frozen corpus and source sentence order. Scores are integer millionths. `withdrawals` maps each scored source ordinal to the verdict after removing all its scores through ordered `[source_ordinal, class_index]` pairs. Remaining fields give run identity, policy, budget, source sensitivity, logical attempt and success counts, number of NLI pairs, quote integrity numerator/denominator, and annotation observations. Numeric files use finite JSON numbers, with null for undefined ratios. Canonical run traces retain their integer-only format.

The public verifier rebuilds aggregate statistics and bootstrap intervals independently, checks identities, reclassifies source withdrawals from integer scores, and replays every SciFact trace and gold association. With the prepared data it also replays Climate lexical search, sentence acquisition, numeric source bindings, and annotation-based observations. The model is not rerun by verification. Unsigned checksums provide consistency checks. They do not establish who produced an internally consistent set of scores.

## Site data contract

The archived browser payload begins at `archive/scifact-demo/index.json` and contains one relative path per saved claim. The index contains `schema_version`, `claims`, `policies`, `budgets`, `threshold`, `corpus_digest` and `model_digest`. Every claim entry contains `query_id`, `claim` and `path`, such as `claims/123.json`. The 40 eligible SciFact queries with lowest SHA-256 of their original ID are selected without inspecting outcomes.

A claim file has `schema_version, query_id, claim, label, cluster_id, runs, sources`. The keys of `runs` have the form `fixed-4`, `first_hit-8`, `challenge-4` or `guarded-8`, covering all eight combinations. Each value is a complete investigation run. The shared `sources` object maps each source ID from any successful search hit or read across the eight runs to its full source object. The full-corpus digest in the run binds the original benchmark corpus. This per-claim subset supports literal quote and snippet checks. Reproducing the search ranking requires the full released corpus.

Each full SciFact trace group has `schema_version, query_id, runs`. Its `runs` values match the demo when that query is selected. `archive/scifact-demo/summary.json` is byte-identical to the full JSON results summary. It contains `schema_version, threshold, model_digest, datasets`. Each dataset contains `queries, clusters, largest_cluster, configs, contrasts`. Configurations contain point statistics, confusion rows ordered by the gold class index, all five prediction columns, `per_class` and `intervals`. Interval axes are `component` and, for Climate, `claim`. Each statistic has `low, high, defined_draws`. Contrast keys follow `challenge-8_minus_fixed-8`. Each contains `difference` and `intervals`.
