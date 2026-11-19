# Running the local verifier

Install the model extra from a checkout, then copy the seven pinned files into a dedicated directory. The model download is about 580 MB. The Hugging Face download cache lives outside this directory.

```sh
python -m pip install '.[model]'
python - <<'PY'
import json
import shutil
from pathlib import Path
from huggingface_hub import hf_hub_download

lock = json.loads(Path("data/model-lock.json").read_text())
destination = Path(".artifacts/model")
destination.mkdir(parents=True, exist_ok=True)
for name in lock["files"]:
    cached = hf_hub_download(lock["model_id"], name, revision=lock["revision"])
    shutil.copyfile(cached, destination / name)
PY
```

The verifier checks every file's byte length and SHA-256 against the committed model lock before loading the local fast tokenizer and safetensors weights. Other files in the model directory cause an error. The pinned model config maps output indices 0, 1 and 2 to contradiction, entailment and neutral. The model card describes training on SNLI and MultiNLI. Overlap between benchmark material and the model's training or pretraining data is unknown. [Model lock](../data/model-lock.json) · [Pinned config](https://huggingface.co/cross-encoder/nli-deberta-v3-small/blob/fa2804872c3b4bd748f38c0185cc85775361e735/config.json) · [Model card](https://huggingface.co/cross-encoder/nli-deberta-v3-small)

Each pair places the selected source sentence first and the complete claim second. The 512-token joint limit includes special tokens. Right truncation shortens only the premise. When a claim leaves no room for a premise token, verification fails. Fast-tokenizer sequence offsets identify the last retained source character. The scored quote is the contiguous original prefix to that offset. Inference uses evaluation mode and float32. Softmax scores are rounded by largest remainder to three integer millionths summing to one million, with class-index tie breaking. Treat the scores as model judgments with unknown probability calibration.

## Reusing scores

```python
from agentresearch.model import NeuralVerifier, ScoreCache
from agentresearch.retrieval import BM25Index

index = BM25Index(corpus)
model = NeuralVerifier(corpus, ".artifacts/model", "data/model-lock.json",
                       device="cpu", batch_size=16)
claim = "Leaf exposure affects photosynthesis."
spans = [span for hit in index.search(claim, limit=4)
         for span in index.read(claim, hit["source_id"])]
with ScoreCache(".artifacts/scores.sqlite", model) as cache:
    scores = cache.verify(claim, tuple(spans))
```

For larger experiments, pass bounded chunks of `(claim, span)` pairs from different claims to `cache.verify_many(pairs)`. The cache reuses complete input, corpus and model bindings. It stores the effective source span, its integer scores and an integrity checksum. Caller order is preserved. The model's `metadata` records the file lock, encoding procedure, dtype, library versions, device and requested batch size. Its `scored_pairs` and `truncated_pairs` counters describe physical scoring in this process. Cache hits do not increment them. Record these separately from logical tool usage and alongside the experiment's batching procedure.

Replaying stored integer scores is exact. Fresh neural inference can differ numerically across devices, libraries or batch shapes, including differences in the final rounded scores. Use the measured run's score ledger for exact decision replay.
