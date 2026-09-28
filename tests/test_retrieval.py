"""
Offline tests for agentkit/retrieval (topic chunker + BM25 index) and the
gather_dfm_standards action.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] Text under min_words becomes one passage without asking the splitter
  [ ] A splitter answer that is a verbatim suffix splits the block; the suffix starts the next block
  [ ] A splitter answer that is NOT a suffix (a paraphrase) is rejected and the block kept whole
  [ ] A splitter that raises is recorded ("splitter_failed_kept_block"), not treated as "one topic"
  [ ] Every passage's start_char/end_char slice the source back to exactly its text
  [ ] A word-budget cut never lands inside a Markdown table or an open code fence
  [ ] BM25: "bend radius ... bend" ranks "Sheet metal bend rules" above "Hole and thread callouts"
  [ ] distinct_by="name" keeps one hit per name, preferring the active record on ties
  [ ] plain_text() turns platform HTML descriptions into clean lines
Hint: a scripted splitter is any object with trailing_topic(block) -> str.
"""

import pytest

from agentkit.retrieval import EvidenceIndex, HeadingSplitter, chunk_by_topic, plain_text

# Write your tests below.
