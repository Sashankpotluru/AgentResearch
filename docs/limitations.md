# Interpreting a malware investigation

**Check the excerpt before accepting a verdict.** The model learned general natural-language inference and uses a 0.90 threshold selected on science training claims. On 64 positive-derived malware claims, the eight-source fixed policy returned 45 refutations and 19 conflicts. Unrelated behaviors can receive high contradiction scores. The release measures support yield and dependence on sources. It has no negative class from which to estimate balanced factual accuracy. [Model results](evaluation.md) · [Model details](model.md)

**A source represents a cited publication.** The text comes from MITRE procedure summaries. Several publications may draw on the same report, observation or indicator. Removing a source tests whether its scores change the model’s decision. Agreement among retained summaries cannot establish that the publications provide independent corroboration. [Corpus construction](datasets.md) · [Decision rules](method.md)

**Retrieval targets depend on the annotation process.** ATT&CK supplies both the family–technique relationships behind the questions and the reference labels. Family-filtered retrieval has access to catalogue membership. TRAM can omit relevant techniques and includes historical labels, explicit identifiers and extraction artifacts. Empty labels were excluded. “Ransom” in a title or description defines a text-based slice. [Pinned protocol](../experiments/cyber-protocol.json) · [Post-hoc sensitivity analysis](../experiments/cyber-diagnostic-protocol.json)

**Verification has a boundary.** The browser checks saved search, read and score bindings within each portable bundle. Full-corpus replay requires the Python verifier and release corpus. Hashes detect changed bytes relative to the manifest. An unsigned set of consistent artifacts cannot authenticate the producer or establish physical execution of a model. Fresh scores may vary across hardware and batch shape. [Format](format.md) · [Reproduction guide](reproduce.md)

[Archived limitations for the science study](legacy-limitations.md)
