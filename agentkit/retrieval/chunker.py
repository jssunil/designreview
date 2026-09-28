"""
chunk_by_topic(): split long text into topic-coherent passages without
letting an LLM invent boundaries.

Algorithm (per block of up to `word_budget` words):
1. Cut the block at the word budget, but never inside a Markdown heading,
   an open code fence, or a table -- move the cut back to that structure's start.
2. Ask the splitter for the *trailing* topic: the exact text where a second
   topic begins inside the block (or "" if the block is one topic).
3. Accept the answer only if it is a verbatim suffix of the block. Anything
   else (a paraphrase, a hallucinated sentence) is rejected and the block is
   kept whole -- the model can locate a boundary, never write one.
4. On a split, the first topic becomes a passage and the trailing text rolls
   into the next block (no overlap tokens wasted).
Blocks shorter than `min_words` are passed through without asking anyone.

Every passage records how its boundary was decided (`boundary_note`) and its
exact character/word offsets in the source, so a passage can always be
traced back to the text it came from.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

_WORD = re.compile(r"\S+")
_HEADING = re.compile(r"(?m)^#{1,6}\s+.+$")
_FENCE = re.compile(r"(?m)^```")


@dataclass(frozen=True)
class EvidencePassage:
    text: str
    ordinal: int
    source_id: str = ""
    source_entity: str = ""
    heading: Optional[str] = None
    start_char: int = 0
    end_char: int = 0
    start_word: int = 0
    end_word: int = 0
    boundary_note: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


class TopicSplitter(Protocol):
    def trailing_topic(self, block: str) -> str: ...


class HeadingSplitter:
    """Offline fallback: a second Markdown heading starts a new topic."""

    def trailing_topic(self, block: str) -> str:
        heads = list(_HEADING.finditer(block))
        return block[heads[1].start():].strip() if len(heads) > 1 else ""


SPLIT_PROMPT = """You split documents into topics.

Here is one block of a document:
---
{block}
---
If the block clearly contains more than one distinct topic, reply ONLY with the exact text of the
second part, copied character for character from the block, starting at the first sentence or
heading of the new topic. Keep supporting details, examples and results with the topic they belong to.
If it is one topic, reply with nothing. No labels, no fences, no explanation."""


class GatewayTopicSplitter:
    """Asks the project's LLM gateway (cheap tier) where the second topic starts."""

    def __init__(self, gateway: Any, tier: str = "fast", max_tokens: int = 2048):
        self.gateway, self.tier, self.max_tokens = gateway, tier, max_tokens

    def trailing_topic(self, block: str) -> str:
        return (self.gateway.call(SPLIT_PROMPT.format(block=block), temperature=0.0,
                                  max_tokens=self.max_tokens, tier=self.tier) or "").strip()


def plain_text(value: str) -> str:
    """HTML fragment -> readable text (platform descriptions are HTML)."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", html.unescape(text))
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _heading_of(text: str) -> Optional[str]:
    m = re.search(r"(?m)^#{1,6}\s+(.+)$", text)
    return m.group(1).strip() if m else None


def _safe_cut_point(source: str, start: int, nominal_end: int) -> int:
    """Move a word-budget cut back so it never opens a heading's section
    fragment, an unclosed code fence, or the middle of a table."""
    window = source[start:nominal_end]
    heads = list(_HEADING.finditer(window))
    if heads and heads[-1].start() > 0:
        return start + heads[-1].start()
    fences = [m.start() for m in _FENCE.finditer(window)]
    if len(fences) % 2 and fences[-1] > 0:
        return start + fences[-1]
    line_start = source.rfind("\n", start, nominal_end) + 1
    if source[line_start:nominal_end].lstrip().startswith("|"):
        table_start = line_start
        while table_start > start:
            prev = source.rfind("\n", start, table_start - 1) + 1
            if not source[prev:table_start].lstrip().startswith("|"):
                break
            table_start = prev
        if table_start > start:
            return table_start
    return nominal_end


def _word_index(source: str, char_offset: int) -> int:
    return len(_WORD.findall(source[:char_offset]))


def chunk_by_topic(text: str, *, word_budget: int = 512, min_words: int = 40,
                   splitter: Optional[TopicSplitter] = None, source_id: str = "", source_entity: str = "",
                   meta: Optional[Dict[str, Any]] = None) -> List[EvidencePassage]:
    if not text or not text.strip():
        return []
    splitter = splitter or HeadingSplitter()
    mode = type(splitter).__name__
    raw: List[tuple] = []  # (text, note, start, end)
    pos = next((m.start() for m in _WORD.finditer(text)), len(text))
    while pos < len(text):
        words = list(_WORD.finditer(text, pos))
        if not words:
            break
        nominal = len(text) if len(words) <= word_budget else words[word_budget - 1].end()
        end = _safe_cut_point(text, pos, nominal)
        if end <= pos:  # structure right at the start: use the plain budget cut
            end = nominal
        chunk = text[pos:end].strip()
        chunk_start = pos + text[pos:end].find(chunk)
        nxt = re.search(r"\S", text[end:])
        after = end + nxt.start() if nxt else len(text)
        note: Dict[str, Any] = {"mode": mode, "block_words": len(chunk.split())}

        second = ""
        if len(chunk.split()) < min_words:
            note["outcome"] = "below_min_words"
        else:
            try:
                second = splitter.trailing_topic(chunk).strip()
                note["outcome"] = "second_topic_returned" if second else "one_topic"
            except Exception as e:  # a failed split is not "one topic" -- record it
                note.update(outcome="splitter_failed_kept_block", error=f"{type(e).__name__}: {e}"[:200])

        is_suffix = bool(second) and chunk.rstrip().endswith(second.rstrip())
        split_at = chunk.rstrip().rfind(second.rstrip()) if is_suffix else -1
        if split_at > 0 and chunk[:split_at].strip():
            first = chunk[:split_at].strip()
            note.update(outcome="suffix_rolled_over", suffix_words=len(second.split()))
            raw.append((first, note, chunk_start, chunk_start + split_at))
            pos = chunk_start + split_at
        else:
            if second and not is_suffix:
                note["outcome"] = "non_suffix_rejected_kept_block"
            elif second:
                note["outcome"] = "suffix_is_whole_block_kept"
            raw.append((chunk, note, chunk_start, chunk_start + len(chunk)))
            pos = after

    return [EvidencePassage(text=t, ordinal=i, source_id=source_id, source_entity=source_entity,
                            heading=_heading_of(t), start_char=s, end_char=e,
                            start_word=_word_index(text, s), end_word=_word_index(text, e),
                            boundary_note=n, meta=dict(meta or {}))
            for i, (t, n, s, e) in enumerate(raw)]


def fixed_span_passages(text: str, *, words: int = 40, source_id: str = "") -> List[EvidencePassage]:
    """Baseline: fixed-size word windows (a control to compare topic chunking against)."""
    toks = text.split()
    return [EvidencePassage(" ".join(toks[i:i + words]), n, source_id=source_id,
                            boundary_note={"mode": "fixed_span"})
            for n, i in enumerate(range(0, len(toks), words))]
