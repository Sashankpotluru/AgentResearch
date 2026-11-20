# Malware evidence evaluation

The release measures canonical source-record retrieval, technique ranking and model decisions separately. The original protocol fixes the source revisions, deduplication, sampling, baseline parameters, policies, budgets and comparisons. A later protocol specifies the identifier diagnostic. [Original protocol](../experiments/cyber-protocol.json) · [Diagnostic protocol](../experiments/cyber-diagnostic-protocol.json) · [Machine-readable results](../results-cyber/summary.json)

## ATT&CK canonical source-record Recall@5

Each of 10,739 family–technique pairs supplies a query. Its gold set contains the canonical source records assigned to its procedure summaries. Duplicate prose pools references across relationships, so a selected URL can differ from the queried relationship’s own citation. Mean canonical source-record Recall@5 averages the fraction of each query’s gold records in its first five hits. Global BM25 ranks all 1,120 records. Family-filtered BM25 restricts candidates using known family membership. [Gold-set construction](datasets.md)

| Population | Queries | Global BM25 | Family-filtered BM25 |
| --- | ---: | ---: | ---: |
| All 747 families | 10,739 | 97.625% | 99.208% |
| Description contains “ransom”, 77 families | 1,527 | 95.285% | 98.166% |

The overall gain was 1.583 percentage points (95% paired family-bootstrap interval 1.188 to 2.002). The filter uses extra catalogue metadata. High overlap on constructed queries leaves independent factual QA performance unknown. [Canonical source-record rankings](../results-cyber/attack-rankings.json.gz)

## Matching TRAM sentences to techniques

The retrieval target is the set of labeled techniques for each report sentence. Candidate definitions comprise 706 active techniques and two historical IDs required by TRAM. Word BM25, character trigram TF-IDF and RRF rank the same candidates. Character TF-IDF fits its vocabulary and weights to the technique definitions. Unlabeled rows were excluded. [Data processing](datasets.md)

| Original run, 4,036 unique labeled sentences | Mean Recall@5 | Hit@1 | Hit@5 |
| --- | ---: | ---: | ---: |
| Word BM25 | 22.992% | 11.174% | 26.685% |
| Character TF-IDF | **31.805%** | 14.222% | 36.026% |
| RRF | 29.047% | 13.355% | 33.325% |

RRF gained 6.055 percentage points over word BM25 (95% paired interval 4.892 to 7.185). Character TF-IDF performed best overall. The 149 reports form 145 connected components through shared sentences, the largest containing 189 items. The ransomware-title slice includes 557 sentences in 22 components. Bootstrap intervals use 2,000 paired draws with seed 17. Hit@5 credits any retrieved gold technique, while Recall@5 credits the fraction of gold techniques retrieved. [Per-query rankings](../results-cyber/tram-rankings.json.gz)

### Post-hoc identifier-masked diagnostic

After examining the original rankings, the diagnostic removed literal technique IDs from scoring text only. Of 4,036 sentences, 254 contained an explicit identifier. Candidate definitions, original sentences, query IDs, gold sets and parameters stayed fixed. Top-five order changed for 0, 32 and 31 queries respectively. These are repeated measurements on the original sample. [Diagnostic protocol](../experiments/cyber-diagnostic-protocol.json) · [Diagnostic rankings](../results-cyber/tram-id-masked-rankings.json.gz)

| Masked run, same 4,036 sentences | Mean Recall@5 |
| --- | ---: |
| Word BM25 | 22.992% |
| Character TF-IDF | **31.823%** |
| RRF | 29.022% |

## Model decisions and costs

The benchmark selects 64 positive-derived claims, 32 from each side of the ransomware-description filter. Stable ID hashes select cases before examining outcomes. The demo separately selects cases from 24 distinct families. Each case has four policies and two budgets, giving 512 benchmark runs and 192 demo runs. The original model threshold of 0.90 transfers from an earlier SciFact calibration. [Sample IDs](../results-cyber/selection.json) · [Model identity](../results-cyber/model.json) · [Canonical runs](../results-cyber/traces/)

| Eight-source policy | Cases | Supports | Refutes | Conflict | Abstain | Mean reads | Pivotal cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed | 64 | 0 | 45 | 19 | 0 | 8.00 | 12 |
| First hit | 64 | 7 | 52 | 5 | 0 | 1.48 | 64 |
| Challenge | 64 | 0 | 44 | 20 | 0 | 8.00 | 14 |
| Guarded | 64 | 0 | 44 | 6 | 14 | 8.00 | 14 |

The model frequently assigned a high contradiction score to a different behavior discussed in the same corpus. A high score can therefore produce a spurious refutation. All four eight-source configurations returned zero insufficient assessments. Guarded abstains on source-sensitive base verdicts. Logical reads, verifications and scored sentence pairs count tool usage in the trace. Calibration, precision on negatives and balanced factual accuracy require additional evidence. [All budgets, costs and cohorts](../results-cyber/summary.json) · [Source-dependence method](method.md)

The verifier recomputes rankings, intervals and decisions against the released prepared data and checks browser files against the complete canonical runs. Strict intake also starts from the pinned upstream downloads. [Verification commands](reproduce.md) · [Archived science evaluation](legacy-evaluation.md)
