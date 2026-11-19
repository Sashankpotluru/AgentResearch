# Investigation format, version 1

A run records a claim, the corpus and model identities, the policy configuration, tool attempts, scored evidence, usage and the final assessment. The run and corpus are separate JSON files. Replay and export them from an installed package with:

```sh
agentresearch audit run.json corpus.json
agentresearch report run.json corpus.json > investigation.md
```

## Corpus and exact quotes

The corpus envelope has `schema_version: 1` and an ordered `sources` array. A source contains `source_id`, `title`, `text`, ordered `sentences` and a nullable `url`. Each sentence has an ID string and inclusive-start, exclusive-end integer offsets. Source IDs are unique across the corpus. Sentence IDs are unique within each source. Sentences occupy nonempty, nonoverlapping, ordered ranges in the original source text.

Offsets count Unicode code points, including newlines. Python uses `text[start:end]`. JavaScript uses `Array.from(text).slice(start,end).join("")`. Combining marks and astral characters must retain their original encoding. Source text, IDs and titles are data.

A span contains `source_id`, `sentence_id`, `start`, `end`, `quote` and `source_digest`. Its identity is the hexadecimal SHA-256 of this complete canonical span object. The source digest hashes the complete canonical source object. The corpus digest hashes the complete canonical corpus envelope, including source order. Reads return whole sentences. A verifier can shorten the end to the actual retained premise prefix. Every reported quote must exactly match its source slice.

A scored span wraps the effective span in `span` with integer `contradiction`, `entailment`, `neutral` and string `model_digest`. Three scores sum to 1,000,000. The model integration supplies its configuration identity. Scores are model judgments.

## Retrieval and scheduling

The lexical index uses lowercase Unicode word tokens and the committed stop list in `retrieval.py`. Document frequencies count sources containing a term. A document contains two copies of the title followed by the body. Each distinct in-vocabulary query term contributes BM25 with `k1=1.2`, `b=0.75` and `idf=ln(1+(N-df+0.5)/(df+0.5))`. Documents with a positive score rank by descending unrounded score then source ID. The transport score rounds the result multiplied by one million using ties-to-even rounding. The snippet is the first 180 code points of the source body. Relevance and challenge sum terms in lexical order. Focus uses the four highest-IDF matching terms, breaking IDF ties lexically. Challenge boosts relevance by 25% per distinct cue in the indexed source, up to three cues. The cues are no, not, never, however, despite, failed, lack and without.

Each read selects up to two sentences using the sum of corpus IDFs for distinct matching terms. Ties preserve original sentence order. Results are returned in relevance order. Source budgets range from one to eight. A successful read always has a corresponding verification attempt before the investigation can complete.

- `fixed` consumes the relevance ranking to exhaustion or budget.
- `first_hit` stops early after any substantive provisional verdict.
- `challenge` searches again after three reads or exhaustion of the initial ranking if budget remains. A substantive provisional verdict selects challenge search. An insufficient verdict selects focus. New ranking entries take priority over unused original entries.
- `guarded` uses the challenge acquisition policy and applies the stability gate after scoring.

## Decisions and withdrawal

Decision objects have `verdict`, `witnesses` and `internal_conflict`. Each witness is a span identity. A side activates at or above the integer threshold. Unique source/sentence pairs contribute at most one score record. The strongest witness per side within each source wins, with ties ordered by source ID, sentence ID, start, end and span identity. Distinct-source conflict maximizes the sum of supporting and refuting scores. Equal totals prefer the strongest supporting score and its stable witness order, then the strongest refuting score and its stable witness order. Witnesses list support before refutation. Two active sides confined to one source yield INSUFFICIENT with the internal-conflict flag. The other base verdicts are SUPPORTS, REFUTES, CONFLICT and INSUFFICIENT.

The assessment contains `base`, `withdrawals` (a source-ID-to-decision map), `pivotal_sources`, `stable` and `final`. Withdrawing a source excludes every score from that source. A pivotal source changes the base **verdict** when removed. A changed witness alone leaves the verdict stable. Any pivotal source triggers ABSTAIN under the guarded policy. An abstention has no final witnesses. Base evidence and withdrawal decisions remain available.

## Trace, validation and resume

Run fields are `schema_version`, `run_id`, `claim`, `config`, `corpus_digest`, `model_digest`, `status`, `events`, `scores`, `assessment` and `usage`. Config contains `policy`, `budget`, `threshold` and `model_digest`. Automation may supply a run ID. The default is the digest of the ordered array [claim, config, corpus digest].

An event contains `sequence`, `tool`, `input`, `output`, `attempt`, `error`, `previous_hash` and `hash`. Sequence and attempt count from one. The first event has a null previous hash. Each hash covers the complete event except its own hash. Search input contains claim, mode and limit. Read input contains claim and source_id. Verify input contains claim and full read spans. Successful outputs are arrays of hits, spans or scored spans. A search hit has source_id, score and snippet. Failures store a null output and one of transient, permanent or invalid_output. Only transient failures receive retries, up to three attempts total per action.

Statuses are running, complete and failed. Running and failed traces have a null assessment and expose any scores already committed. Usage counts attempts and successes by tool, unique successfully read sources, and successfully returned NLI pairs. Tool attempts and checkpoint writes share a SQLite write transaction. A crash before commit can repeat a physical read-only tool invocation. Logical usage counts cover committed attempts. A database serializes writers, including writers for different runs. Use separate databases for independent worker parallelism.

Canonical JSON sorts object keys by Unicode code-point order and serializes UTF-8 without ASCII escaping, whitespace separators or Unicode normalization. Allowed values are null, booleans, strings, arrays, objects with string keys, and integers in ±(2⁵³−1). Duplicate JSON keys, floating-point values and unknown schema fields are rejected. Booleans cannot stand in for integers. Run imports default to a 5 MiB limit.

Validation replays scheduling, search results, sentence selection, exact source slices, score/event binding, usage, decisions and all withdrawal outcomes. The hash chain checks internal consistency. Anyone able to rewrite an unsigned trace can recompute its hashes. Authenticating the producer or confirming that the recorded model physically produced a score requires separate provenance.
