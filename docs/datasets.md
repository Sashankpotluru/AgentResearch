# Preparing the benchmark data

From a checkout with the package installed, fetch and verify the two releases. Local preparation can run offline once the raw files exist.

```sh
agentresearch prepare --download --raw-dir .artifacts/raw --out-dir .artifacts/prepared
```

The downloader verifies HTTPS certificates using Python’s configured trust roots. If these are empty, it uses the certifi bundle when installed. An administrator-provided CA bundle can also be selected through `SSL_CERT_FILE`.

The source pins specify the exact URLs, byte lengths and SHA-256 hashes. Preparation checks both releases before reading their data. For SciFact, it reads three named regular files inside the gzip tar archive. The output manifest records the raw pins, the digest of each source corpus, each generated file's SHA-256, split sizes, label counts and connected-component sizes. [Source pins](../data/sources.json)

| Dataset | Runtime corpus | Evaluation inputs |
| --- | --- | --- |
| SciFact | 5,183 scientific abstracts | 537 calibration claims and 300 development claims |
| Climate-FEVER | 5,240 distinct supplied evidence sentences grouped into 1,344 article sources | All 1,535 released claims |

SciFact source text joins original abstract sentences with newlines. Sentence IDs retain zero-based indices. Titles are separate. All 809 original training rows are in `scifact/train.jsonl`. Calibration excludes the 272 rows citing any document cited by a development claim. Its labels comprise 219 supports, 102 refutes and 216 insufficient. The development split contains 124 supports, 64 refutes and 112 insufficient. The original test split has no public labels and is excluded. Development uncertainty groups claims connected through shared cited documents into 247 components, the largest containing four claims. [Wadden et al., 2020](https://aclanthology.org/2020.emnlp-main.609/) · [SciFact release](https://github.com/allenai/scifact)

Climate-FEVER supplies five annotated candidates per claim. Search indexes the pooled evidence across every claim. Article title hashes identify sources. Each original evidence ID identifies a distinct sentence, sorted lexically within its article. The four claim labels map to 654 supports, 253 refutes, 474 insufficient and 154 conflict. The last category corresponds to the release's disputed label. Shared evidence IDs connect the 1,535 claims into 410 components. One component contains 1,040 claims, which limits cluster-based statistical inference. This retrieval task covers the released candidate pool rather than all of Wikipedia. [Diggelmann et al., 2020](https://arxiv.org/abs/2012.00614) · [Original release](https://github.com/tdiggelm/climate-fever-dataset)

## Runtime and evaluation files

Pass only the dataset's `corpus.jsonl` to the agent. Each line is one source record conforming to the [corpus format](format.md). Claim text, evidence associations, gold labels, cited sources and component IDs reside in separate evaluation files. The programmatic loader checks file and corpus digests.

```python
from pathlib import Path
from agentresearch.data import load_dataset

corpus, queries = load_dataset(Path(".artifacts/prepared"), "scifact", "dev")
```

Supported selections are `scifact/train`, `scifact/calibration`, `scifact/dev` and `climate/test`. Query IDs are original IDs represented as strings. Each evidence entry contains a source ID, an ordered list of sentence IDs and its annotated label. SciFact entries preserve alternative rationale sets. Climate entries preserve the labels of all supplied candidates, including insufficient candidates. The corpus contains only source material. For redistribution terms, see the [third-party notices](../THIRD_PARTY_NOTICES.md).
