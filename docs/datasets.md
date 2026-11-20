# Malware and report-sentence datasets

The cybersecurity release uses pinned public JSON files from Enterprise ATT&CK and the Threat Report ATT&CK Mapper (TRAM). The intake checks the byte count and SHA-256 of each data file and license before preparing records. The release manifest lists the prepared inventory, selection, protocols and checksums. [Input pins](../data/cyber-sources.json) · [Manifest](../results-cyber/manifest.json) · [Notices](../THIRD_PARTY_NOTICES.md)

## ATT&CK evidence corpus

Active malware-to-technique relationships produce 10,739 constructed family–technique questions across 747 malware families. Preparation cleans ATT&CK markup, normalizes whitespace and deduplicates procedure descriptions. The 10,695 distinct procedures retain provenance and are grouped into 1,120 source records. When several reference URLs accompany the same procedure, a deterministic choice of the first sorted URL places it under one source. Seven source records use the ATT&CK software page as a fallback when a procedure lacks an external URL. Source text contains MITRE’s summary of the behavior. A publication link identifies its cited reference. Vendor-report full text was never fetched. [Published corpus](../results-cyber/corpus.jsonl) · [Prepared records](../results-cyber/prepared/)

The description substring “ransom”, matched case-insensitively, identifies 77 families and 1,527 queries for one slice. Family descriptions can mention ransomware in different contexts. Interpret this rule as text matching. Each query’s gold publications derive from ATT&CK itself. [Construction rules](../experiments/cyber-protocol.json)

## TRAM annotations and candidate definitions

The pinned TRAM file contains 19,178 raw rows. After excluding blank text and unlabeled rows, 4,070 labeled rows produce 4,036 distinct sentences by whitespace-normalized deduplication. Duplicate sentences contribute the union of their technique labels and report memberships. Missing labels cannot establish negatives. The 149 reports connect into 145 components. The ransomware-title slice includes a sentence whenever any member report title contains “ransom”. [Prepared inputs](../results-cyber/prepared/) · [Original source](https://github.com/center-for-threat-informed-defense/tram/tree/f29793d8d665f7f552898696e00065ef24a29a20)

Rankers use 706 active ATT&CK definitions plus historical T1562.001 and T1574.002. Historical labels occur in 162 items, and 254 sentences explicitly mention a technique ID. Some sentences retain PDF/report extraction artifacts. The separately reported masked diagnostic removes matching IDs only from text passed to the ranking methods. [Evaluation](evaluation.md) · [Diagnostic rules](../experiments/cyber-diagnostic-protocol.json)

The original science release retains its own inputs and annotations. [Archived dataset construction](legacy-datasets.md)
