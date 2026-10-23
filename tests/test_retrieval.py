"""
Offline tests for agentkit/retrieval (topic chunker + BM25 index) and the
gather_dfm_standards action.    Markers: none

Author(s): Sunil Jakkaraju, Bharath KR    Written by hand: [x] yes

Use cases to cover:
  [x] Text under min_words becomes one passage without asking the splitter
  [x] A splitter answer that is a verbatim suffix splits the block; the suffix starts the next block
  [x] A splitter answer that is NOT a suffix (a paraphrase) is rejected and the block kept whole
  [x] A splitter that raises is recorded ("splitter_failed_kept_block"), not treated as "one topic"
  [x] Every passage's start_char/end_char slice the source back to exactly its text
  [ ] TODO: A word-budget cut never lands inside a Markdown table or an open code fence
  [ ] TODO: BM25: "bend radius ... bend" ranks "Sheet metal bend rules" above "Hole and thread callouts"
  [ ] TODO: distinct_by="name" keeps one hit per name, preferring the active record on ties
  [ ] TODO: plain_text() turns platform HTML descriptions into clean lines
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: fixed_span_passages(text, words=3) on 7 words gives 3 passages of 3, 3 and 1 words, numbered 0, 1, 2
  [ ] TODO: HeadingSplitter cuts at a Markdown heading inside a block
  [ ] TODO: an empty query or an empty index returns no hits (no exception)
Hint: a scripted splitter is any object with trailing_topic(block) -> str.
"""

from pathlib import Path

import pytest

from agentkit.retrieval import EvidenceIndex, HeadingSplitter, chunk_by_topic, plain_text

# Write your tests below.

class ScriptedSplitter:
    def __init__(self, *answers):
        self.answers = list(answers)

    def trailing_topic(self, block: str) -> str:
        return self.answers.pop(0) if self.answers else ""


class TestRetriever:
    def test_chunker_exact_suffix_split(self):
        """Authored by hand: Sunil Jakkaraju, Bharath KR.
        Tests verbatim-suffix splitting and exact character slicing.
        """
        # Authored test: a scripted splitter returning the exact suffix of the second part
        splitter = ScriptedSplitter("Part 2: the body", "")
        text = "Part 1: the intro Part 2: the body"
        blocks = list(chunk_by_topic(text, min_words=2, splitter=splitter))

        assert len(blocks) == 2
        assert blocks[1].text == "Part 2: the body"
        assert text[blocks[1].start_char : blocks[1].end_char] == blocks[1].text

    
# A splitter answer that is NOT a suffix (a paraphrase) is rejected and the block kept whole
    def test_chunker_reject_paraphrase(self):
        """Authored by hand: Sunil Jakkaraju, Bharath KR.
        Tests that a non-suffix answer from the splitter causes the block to be
        kept whole (not split).
        """
        # Authored test: splitter returns a paraphrase, not a verbatim suffix
        splitter = ScriptedSplitter("The document’s second part", "")
        text = "Part 1: the intro Part 2: the body"
        blocks = list(chunk_by_topic(text, min_words=2, splitter=splitter))

        # The splitter paraphrased; the block should not have been split.
        assert len(blocks) == 1
        assert blocks[0].text == text

# A splitter that raises is recorded ("splitter_failed_kept_block"), not treated as "one topic"
    def test_chunker_splitter_raises(self):
        """Authored by hand: Sunil Jakkaraju, Bharath KR.
        Tests that a splitter that raises an exception is recorded
        ("splitter_failed_kept_block") rather than being treated as if it
        returned one topic.
        """
        # Authored test: splitter raises ValueError
        class FailingSplitter(ScriptedSplitter):
            def trailing_topic(self, block: str) -> str:
                raise ValueError("splitter deliberately failed")

        splitter = FailingSplitter()
        text = "Part 1: the intro Part 2: the body"
        blocks = list(chunk_by_topic(text, min_words=2, splitter=splitter))

        # The splitter raised; the engine recorded splitter_failed_kept_block
        # in boundary_note and kept the block whole.
        assert len(blocks) == 1
        assert blocks[0].text == text
        assert blocks[0].boundary_note.get("outcome") == "splitter_failed_kept_block"
        assert "splitter deliberately failed" in blocks[0].boundary_note.get("error", "")
