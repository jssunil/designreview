"""
Offline tests for agentkit/retrieval/hierarchy.py (split_by_headers, ParentChildIndex).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] split_by_headers gives one passage per Markdown section with its header path
      ("# A" / "## B" / "### C" -> ["A", "B", "C"]; a new "## D" resets to ["A", "D"])
  [x] a "#" line inside a ``` code fence is not a heading
  [x] text[p.start_char:p.end_char] == p.text for every passage
  [x] recall_parents returns the whole parent record although only one of its sections matched
  [x] each parent appears once, in the rank order of its best child
  [x] a removed parent is dropped from the results (its children are still indexed)
  [x] distinct_by="family" keeps one parent per family; where={"category": ...} filters on inherited meta
  [x] child_meta_fields=["category"] means children carry only that field (plus parent id and header path)
  [x] adding two parents with the same id is an error
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: mget(["a", "missing"]) returns [<doc a>, None]
  [ ] TODO: split_by_headers(max_level=2): a "### C" line stays inside the "## B" passage
  [ ] TODO: strip_headers=True: passage text has no "#" line, and the offsets still slice the source exactly
Hint: ParentChildIndex(child_splitter=section_children(min_words=1000)) keeps each section as one child,
so the results are easy to predict.
"""

import pytest

from agentkit.retrieval import ParentChildIndex, ParentDoc, section_children, split_by_headers

DOC = "# A\nintro\n## B\nb text\n### C\nc text\n## D\nd text\n```\n# not a heading\n```\n"


def test_one_passage_per_section_with_its_header_path_and_exact_offsets():
    passages = split_by_headers(DOC)
    assert [p.meta["header_path"] for p in passages] == [["A"], ["A", "B"], ["A", "B", "C"], ["A", "D"]]
    assert "# not a heading" in passages[-1].text          # a "#" line in a code fence stays in its section
    assert all(DOC[p.start_char:p.end_char] == p.text for p in passages)


def make_index(**kw):
    idx = ParentChildIndex(child_splitter=section_children(min_words=1000), **kw)
    idx.add_documents([
        ParentDoc(
            id="bend",
            entity="DesignStandard",
            meta={"category": "sheet", "family": "metal"},
            text="# Bend rules\n## Radius\nminimum inside bend radius\n## Holes\nhole to edge distance",
        ),
        ParentDoc(
            id="weld",
            entity="DesignStandard",
            meta={"category": "joining", "family": "metal"},
            text="# Weld rules\n## Access\nweld access and bend near welds",
        ),
        ParentDoc(
            id="mold",
            entity="DesignStandard",
            meta={"category": "plastic", "family": "polymer"},
            text="# Molding\n## Draft\ndraft angle for molded ribs",
        ),
    ])
    return idx


def test_recall_returns_the_whole_parent_once_in_rank_order():
    hits = make_index().recall_parents("bend radius", k=5)
    ids = [h.parent.id for h in hits]
    assert ids[0] == "bend" and len(ids) == len(set(ids))    # each parent once, best first
    assert "hole to edge distance" in hits[0].parent.text      # the whole record, not only the matching section
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_removed_parent_is_dropped_and_filters_use_inherited_meta():
    idx = make_index()
    idx.remove("bend")
    assert "bend" not in [h.parent.id for h in idx.recall_parents("bend radius", k=5)]

    idx = make_index()
    assert [h.parent.id for h in idx.recall_parents("bend", k=5, where={"category": "joining"})] == ["weld"]
    assert len(idx.recall_parents("bend weld", k=5, distinct_by="family")) == 1   # bend and weld share "metal"


def test_child_meta_fields_limit_what_children_carry_and_duplicate_ids_fail():
    idx = make_index(child_meta_fields=["category"])
    child = idx.recall_parents("draft angle", k=1)[0].children[0].passage
    assert child.meta.get("category") == "plastic" and "family" not in child.meta

    with pytest.raises(ValueError, match="duplicate parent id"):
        idx.add_documents([ParentDoc(id="bend", entity="DesignStandard", text="again")])

