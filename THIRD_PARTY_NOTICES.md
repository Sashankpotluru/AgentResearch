# Third-party notices

AgentResearch's original software uses the MIT license. The following material retains its source terms.

## MITRE ATT&CK®

© 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of The MITRE Corporation. AgentResearch cleans ATT&CK markup, groups procedure summaries by cited publication, derives family–technique queries and preserves reference links. These excerpts reproduce MITRE’s descriptions. The cited publications retain their respective ownership. [Pinned ATT&CK source](https://github.com/mitre-attack/attack-stix-data/tree/a8db2f52eb933c9c99060c123e088b3326d3a607) · [Full license and disclaimer](data/cyber-licenses/attack-license.txt)

## TRAM

Copyright 2020 The MITRE Corporation. The Center for Threat-Informed Defense’s Threat Report ATT&CK Mapper (TRAM) supplies labeled report sentences. AgentResearch normalizes whitespace, deduplicates sentences, unions report memberships and labels, and publishes derived ranking results. The separate identifier-masked diagnostic removes literal technique IDs from scoring text. [Pinned TRAM source](https://github.com/center-for-threat-informed-defense/tram/tree/f29793d8d665f7f552898696e00065ef24a29a20) · [Apache 2.0 license](data/cyber-licenses/tram-license.txt) · [Original notice](data/cyber-licenses/tram-notice.txt)

## Archived SciFact and S2ORC

SciFact claims and annotations are by David Wadden, Shanchuan Lin, Kyle Lo, Lucy Lu Wang, Madeleine van Zuylen, Arman Cohan and Hannaneh Hajishirzi, *Fact or Fiction: Verifying Scientific Claims* (EMNLP 2020). The authors release claims and annotations under CC BY 4.0. The abstracts come from the Semantic Scholar Open Research Corpus (S2ORC), licensed under Open Data Commons Attribution 1.0. AgentResearch reformats source records, derives sentence offsets and creates separate evaluation splits and model-generated traces. [SciFact paper](https://aclanthology.org/2020.emnlp-main.609/) · [Dataset license](https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/LICENSE.md) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) · [S2ORC](https://github.com/allenai/s2orc) · [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/)

## Climate-FEVER

Thomas Diggelmann, Jordan Boyd-Graber, Jannis Bulian, Massimiliano Ciaramita and Markus Leippold released *CLIMATE-FEVER: A Dataset for Verification of Real-World Climate Claims* (2020). The authors' dataset card marks its license unknown. This project supplies acquisition code and derived numerical results. Researchers can obtain the original text from the authors' repository. [Paper](https://arxiv.org/abs/2012.00614) · [Dataset repository](https://github.com/tdiggelm/climate-fever-dataset) · [Author dataset card](https://huggingface.co/datasets/tdiggelm/climate_fever)

## Neural model

The `cross-encoder/nli-deberta-v3-small` model card specifies Apache License 2.0. Researchers obtain the model and tokenizer files from the publisher. AgentResearch provides their checksums and inference code. [Model card](https://huggingface.co/cross-encoder/nli-deberta-v3-small) · [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)

## PubMed

The optional connector retrieves abstracts and metadata from NCBI for the user's query. Attribution links accompany each source. For reuse of returned material, consult NCBI's guidance and the original publisher. [NCBI policies](https://www.ncbi.nlm.nih.gov/home/about/policies/)
