# `agentkit/retrieval/` — chunking and recall

*Reading order: **5th**. Files: `chunker.py`, `index.py`. Used by the pack's `gather_dfm_standards` action.*

## 10,000-ft view

`chunk_by_topic()` splits long text into topic passages without letting an LLM invent a boundary;
`EvidenceIndex` is a stdlib BM25 keyword index over those passages.

## Why it's written this way

- **The model locates a boundary; it never writes one.** Text is cut into blocks of up to 512 words (never
  inside a Markdown heading, an open code fence, or a table). A splitter names where a second topic starts;
  the answer is accepted **only if it is a verbatim suffix of the block**, and the suffix rolls into the next
  block. A paraphrased or invented answer is rejected and the block kept whole. A splitter that errors is
  recorded as such, not silently treated as "one topic".
- **Every passage is traceable.** Each records how its boundary was decided (`boundary_note`) and its exact
  character/word offsets in the source.
- **Short text costs nothing.** Below `min_words` (40) a text is one passage and no splitter is called — which
  is the case for every `DesignStandard` record today (4–28 words each).
- **BM25, no embeddings.** The records are few and short and the index is rebuilt each run, so an embedding
  model or vector store would add a dependency for no gain. Hits report which terms matched. Repeated query
  terms weigh more; `distinct_by` collapses duplicate records (the seed data repeats standard names across
  departments), preferring the lower `meta["_rank"]` on ties (the pack uses it to prefer active records).

## Key types

| Name | Purpose |
|---|---|
| `EvidencePassage` | text, ordinal, source id/entity, heading, offsets, boundary_note, meta |
| `TopicSplitter` protocol / `GatewayTopicSplitter` / `HeadingSplitter` | who proposes the boundary |
| `chunk_by_topic(text, word_budget=512, min_words=40, splitter=, source_id=, meta=)` | the chunker |
| `fixed_span_passages()` | fixed-window baseline for comparison |
| `EvidenceIndex.add()` / `.recall(query, k, where=, distinct_by=)` | index and search |
| `plain_text(html)` | platform HTML descriptions → clean lines |

## In the design-review pack

`gather_dfm_standards` reads every `DesignStandard` page through the journaled client, indexes name +
description, and recalls with a query built from the change notes and the release blockers. The finding
labels the matches "relevance, not compliance". Live, for the Battery Tray, "Sheet metal bend rules" ranks first.

## How to test

`tests/test_retrieval.py` — a scripted splitter is any object with `trailing_topic(block)`.
