# Investigating a claim

Install the package and the model extra, prepare a corpus and download the locked verifier. From the repository root, follow the [dataset instructions](datasets.md) and [model instructions](model.md).

```sh
agentresearch run --corpus results-cyber/corpus.jsonl \
  --model-dir .artifacts/model --db .artifacts/runs.sqlite \
  --cache .artifacts/scores.sqlite --claim "WannaCry uses Peripheral Device Discovery." \
  --policy challenge --budget 4 --threshold 900000 --run-id wannacry-resume \
  --max-steps 1 > .artifacts/partial.json

agentresearch resume --corpus results-cyber/corpus.jsonl \
  --model-dir .artifacts/model --db .artifacts/runs.sqlite \
  --cache .artifacts/scores.sqlite --run-id wannacry-resume > .artifacts/run.json

agentresearch audit .artifacts/run.json results-cyber/corpus.jsonl
agentresearch report .artifacts/run.json results-cyber/corpus.jsonl > .artifacts/report.md
```

The example uses an published threshold of 0.90, expressed in integer millionths, and pauses after one tool attempt. Omit `--max-steps` to run to completion. Both the corpus and model identities must match on resume. Given a portable run and its corpus, audit and report work offline without the optional neural dependencies. A successful audit replays tool selection, quotes and decisions from recorded scores. The report attributes those scores to the automated verifier. [Investigation format](format.md)

## Optional science connector for PubMed abstracts

```sh
agentresearch pubmed --query 'tree canopy urban shade AND hasabstract' \
  --max-records 10 > .artifacts/pubmed.json
```

Pass this JSON file to `--corpus` in place of a prepared JSONL corpus. ESearch selects up to 50 PubMed identifiers, and EFetch retrieves their records from NCBI over HTTPS. Requests are spaced at least 0.4 seconds apart within the process, limited to 5 MiB per response and use a 15-second socket timeout. Temporary failures allow up to two retries. The optional `--email` value is sent to NCBI as a contact address. Article records without abstract text and book records are omitted. The resulting corpus can be empty. [NCBI E-utilities reference](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html) · [NCBI usage limits](https://eutilities.github.io/site/API_Key/usageandkey/)

Each retained source includes its PMID and record URL. Inline XML markup is rendered as text and abstract sections are joined with newlines. Sentence splitting uses paragraph breaks and punctuation followed by whitespace, so abbreviations can produce imperfect boundaries. This fetch is a bounded search of abstracts. Conclusions depend on the retrieved records.

## MCP stdio

Install the official Python SDK extra with `python -m pip install '.[mcp,model]'`. Configure your MCP host to execute the following program with separate arguments. Use absolute paths for the executable, corpus, model directory and SQLite files when configuring a desktop host.

```sh
agentresearch serve-mcp --corpus results-cyber/corpus.jsonl \
  --model-dir .artifacts/model --db .artifacts/runs.sqlite --cache .artifacts/scores.sqlite
```

| Tool | Inputs | Structured result |
| --- | --- | --- |
| `search_sources` | Claim, search mode, limit 1–12 | Ranked sources and corpus digest |
| `read_source` | Claim and a returned source ID | Up to two exact source spans |
| `investigate` | Claim, threshold in millionths, policy, budget 1–8 | Versioned investigation trace with a stored run ID |
| `audit_run` | A locally stored run ID | Replay status and assessment |

The MCP server requires a file-backed checkpoint because each tool call opens a separate connection. Use a SQLite file path for `--db`. Search and read can run before loading the neural model. Investigation loads the verified model on demand. Tool arguments accept claims up to 10,000 characters and IDs up to 512 characters. The process uses stdout for protocol traffic and stderr for diagnostics. [Official MCP server guide](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server)
