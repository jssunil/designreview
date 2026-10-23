"""
Offline tests for retrieval *quality*, not just retrieval mechanics: recall@k and MRR on a small
hand-labelled set, a relevance floor, query expansion, a stable passage order, a chunking comparison and
context-carrying chunks (agentkit/retrieval/*, gather_dfm_standards in packs/designreview/actions.py).
Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/RESEARCH_GAP_ANALYSIS.md, gaps I10, I11, I12, I14, I16, I17.
Items marked (not built yet) test a feature that does not exist today: write the test, and mark it
@pytest.mark.xfail(strict=True, reason="I#") (or skip) until the feature lands.

Use cases to cover:
  Metrics (I10, built 2026-10-14: agentkit.retrieval.recall_at_k / precision_at_k / reciprocal_rank / evaluate):
  [x] recall@k and MRR on a 3-query hand-made table: exact values
      (e.g. relevant at rank 1, rank 3, not found -> MRR = (1 + 1/3 + 0) / 3 = 4/9)
  [ ] TODO: on the captured Suryodaya standards, the t01 change notes recall "Sheet metal bend rules" within the
      top 5 (works today; note the captured standards are name-only, so each is one short passage)

  Relevance gate (I11, built 2026-10-14: agentkit.retrieval.gate_hits / relevance_band):
  [x] (built 2026-10-15: GateConfig.min_terms, retrieval.toml min_terms = 2) a query that shares only one
      incidental word with a standard ("coffee machine warranty steel" vs a hot-rolled-steel bend rule):
      recall_parents still returns it, gate_hits(..., GateConfig(min_terms=2)) drops it
  [x] "bend radius for steel" (three matched terms) keeps the same standard
  [ ] TODO: scripts/retrieval_eval.py --check stays at or above the floors in retrieval.toml with min_terms = 2
  [x] scores map to correct / ambiguous / incorrect bands at the agreed thresholds

  Query expansion (I12, built 2026-10-14: agentkit.retrieval.expand_query, retrieval.toml [synonyms]):
  [x] a note that says "inside radius" recalls the "minimum bend radius" standard

  Stable order (I14):
  [x] the same hits always come back in the same order (ties broken the same way every time)

  Chunking comparison (I16, not built yet):
  [ ] TODO: fixed 256 words, fixed 512 with overlap, header-aware, and header + topic chunking each give a
      recall@5 / MRR row; the cheapest within noise is the one used

  Context-carrying chunks (I17, built 2026-10-14: ParentChildIndex(context_keys=...), meta["index_prefix"]):
  [ ] TODO: a child passage's indexed text starts with its parent's name and category; the text shown to
      the narrator does not

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: relevance_band with best = 0 or score <= min_score -> "incorrect"; matched = -1 skips the term floor
  [ ] TODO: expand_query keeps the original text first; a phrase that appears inside another word does not match
  [ ] TODO: evaluate() on an empty list does not divide by zero (decide the result and pin it)
Hint: ParentChildIndex().add_documents([ParentDoc(id=..., text=..., meta=..., entity="DesignStandard")]),
then recall_parents(query, k=5). EvidenceIndex is the flat BM25 index; fixed_span_passages is the
fixed-size baseline; standards_query(results) builds the query the action uses.
"""

from types import SimpleNamespace

import pytest

from agentkit.retrieval import (
    EvidenceIndex,
    GateConfig,
    ParentChildIndex,
    ParentDoc,
    chunk_by_topic,
    evaluate,
    expand_query,
    fixed_span_passages,
    gate_hits,
    relevance_band,
    section_children,
)
from packs.designreview.actions import standards_query

BEND_RULE = ParentDoc(
    id="s1",
    entity="DesignStandard",
    meta={"name": "Sheet Metal Bend Rules"},
    text="Minimum inside bend radius shall be at least 1x thickness for hot rolled steel.",
)


def test_recall_and_mrr_on_a_three_query_table():
    table = [
        (["a", "x", "y"], {"a"}),     # relevant at rank 1
        (["x", "y", "b"], {"b"}),     # relevant at rank 3
        (["x", "y", "z"], {"c"}),     # not found
    ]
    out = evaluate(table, k=5)
    assert out["queries"] == 3
    assert out["mrr"] == pytest.approx((1 + 1 / 3 + 0) / 3, abs=1e-4)   # 4/9
    assert out["recall@5"] == pytest.approx(2 / 3, abs=1e-4)


def test_scores_map_to_correct_ambiguous_and_incorrect_bands():
    cfg = GateConfig(keep_ratio=0.5, ambiguous_ratio=0.3)
    assert [relevance_band(s, 10, cfg) for s in (10, 5, 3, 2.9, 0)] == [
        "correct", "correct", "ambiguous", "incorrect", "incorrect"
    ]
    hits = [SimpleNamespace(score=s) for s in (8, 3, 1)]
    assert [band for _, band in gate_hits(hits, cfg)] == ["correct", "ambiguous"]


def test_min_terms_gate_drops_a_one_word_match_but_keeps_a_real_one():
    idx = ParentChildIndex()
    idx.add_documents([BEND_RULE])
    incidental = idx.recall_parents("coffee machine warranty steel", k=5)
    assert [h.parent.id for h in incidental] == ["s1"]            # BM25 alone keeps it ("steel")
    assert gate_hits(incidental, GateConfig(min_terms=2)) == []   # the gate drops it
    real = idx.recall_parents("bend radius for steel", k=5)
    assert [h.parent.id for h, _ in gate_hits(real, GateConfig(min_terms=2))] == ["s1"]


def test_query_expansion_and_a_stable_order():
    synonyms = {"inside radius": ["minimum", "bend", "radius"]}
    expanded = expand_query("the inside radius grew", synonyms)
    assert expanded.startswith("the inside radius grew") and expanded.endswith("minimum bend radius")

    idx = ParentChildIndex()
    idx.add_documents([
        ParentDoc(id=f"s{i}", entity="DesignStandard", meta={"name": f"Bend rules {i}"}, text="bend radius rule")
        for i in range(4)
    ])   # four exact ties
    first = [h.parent.id for h in idx.recall_parents("bend radius", k=4)]
    assert all([h.parent.id for h in idx.recall_parents("bend radius", k=4)] == first for _ in range(5))

