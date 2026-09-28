"""
EvidenceIndex: an in-memory BM25 keyword index over EvidencePassages.

Stdlib only -- no embeddings, no vector store. Passages are small and few
(one seat's records), so exact-term BM25 with light normalisation is both
sufficient and fully explainable: each hit reports which query terms it
matched. `recall()` can filter on any passage attribute or meta key.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from agentkit.retrieval.chunker import EvidencePassage

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOPWORDS = frozenset("""a an and are as at be by for from has have in is it its of on or that the this to was
were with which will not no into per than then there these those via""".split())


def terms(text: str) -> List[str]:
    out = []
    for t in _TOKEN.findall((text or "").lower()):
        if t in STOPWORDS or len(t) < 2 and not t.isdigit():
            continue
        # light stemming: plural/verb endings, enough for "rules"/"rule", "welding"/"weld"
        for suf in ("ing", "es", "s", "ed"):
            if len(t) > len(suf) + 2 and t.endswith(suf) and not t[-len(suf) - 1].isdigit():
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


@dataclass
class Hit:
    passage: EvidencePassage
    score: float
    matched: List[str]


class EvidenceIndex:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.passages: List[EvidencePassage] = []
        self._tf: List[Counter] = []
        self._df: Counter = Counter()

    def __len__(self) -> int:
        return len(self.passages)

    def add(self, passages: Iterable[EvidencePassage]) -> int:
        n = 0
        for p in passages:
            tf = Counter(terms(p.text))
            self.passages.append(p)
            self._tf.append(tf)
            self._df.update(tf.keys())
            n += 1
        return n

    def _matches(self, p: EvidencePassage, where: Optional[Dict[str, Any]]) -> bool:
        for key, want in (where or {}).items():
            have = getattr(p, key, None) if hasattr(p, key) else p.meta.get(key)
            if isinstance(want, (list, tuple, set, frozenset)):
                if have not in want:
                    return False
            elif have != want:
                return False
        return True

    def recall(self, query: str, k: int = 5, where: Optional[Dict[str, Any]] = None,
               min_score: float = 0.0, distinct_by: Optional[str] = None) -> List[Hit]:
        """Top-k passages by BM25. A term repeated in the query counts more
        (query term frequency, saturated like document tf). `distinct_by`
        keeps only the best hit per value of that meta key/attribute
        (e.g. "name", to collapse duplicate records)."""
        qtf = Counter(terms(query))
        q = list(qtf)
        if not q or not self.passages:
            return []
        n = len(self.passages)
        avg = sum(sum(tf.values()) for tf in self._tf) / n or 1.0
        hits: List[Hit] = []
        for p, tf in zip(self.passages, self._tf):
            if not self._matches(p, where):
                continue
            length = sum(tf.values()) or 1
            score, matched = 0.0, []
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                doc_part = f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / avg))
                query_part = qtf[t] * (self.k1 + 1) / (qtf[t] + self.k1)
                score += idf * doc_part * query_part
                matched.append(t)
            if score > min_score:
                hits.append(Hit(p, round(score, 4), matched))
        # Ties: a lower meta["_rank"] wins (callers use it for "prefer active records").
        hits.sort(key=lambda h: (-h.score, h.passage.meta.get("_rank", 0), h.passage.source_id, h.passage.ordinal))
        if distinct_by:
            seen, unique = set(), []
            for h in hits:
                if hasattr(h.passage, distinct_by):
                    key = getattr(h.passage, distinct_by)
                else:
                    key = h.passage.meta.get(distinct_by)
                key = str(key).strip().lower() if key is not None else id(h)
                if key not in seen:
                    seen.add(key)
                    unique.append(h)
            hits = unique
        return hits[:k]
