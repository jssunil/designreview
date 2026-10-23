"""
Offline tests for agentkit/telemetry (spans, run tree, metrics, export).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] span(...) with no active tracer does nothing and records nothing
  [x] Nested spans get the right parent_id; the root's trace_id is its own span_id
  [x] dotted_order of a child starts with its parent's dotted_order + "."
  [x] An exception inside a span marks it status "error" and is re-raised unchanged
  [x] An unknown kind ("banana") raises ValueError
  [ ] TODO: bind_context(fn) bound in the submitting thread keeps the parent in pool threads
  [ ] TODO: 8 threads x 200 spans: no lost spans, all ids unique
  [x] build_tree nests children under parents, orders them by start, keeps an orphan as a root
  [x] percentile is nearest-rank: p50 of 10..100 is 50, p95 is 100, empty list gives None
  [ ] TODO: summarize counts errors (raised or ok=False) and totals LLM tokens and cache hits
  [ ] TODO: export_run then load_telemetry round-trips (format "agentkit-telemetry-v1")
  [ ] TODO: A handler's on_tool_end is called; a handler that raises does not break the run
  [ ] TODO: Offline batch: every run has telemetry.json with one "task" root; tool spans == journal lines
  [ ] TODO: Grading gives the same score.json with telemetry.json deleted
  [ ] TODO: A routed task has a "route" step span under the task span; the router's file-list read is a tool span
      under it
  [ ] TODO: Each "llm" span has the tier, the provider and the model of the call (never the prompt or a key)
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: percentile([5], 95) == 5 and percentile([None, 3, 1], 50) == 1 (None values are left out)
  [ ] TODO: walk(build_tree(...)) gives each span once, with depth 0 for roots
  [ ] TODO: @traced("tool") on a function records one span per call with the function name as default
Hint: Tracer("r1"), `with use_tracer(t): with span("task", "x"): ...`, then look at t.spans.
For the batch cases use the offline batch (see tests/README.md, "The whole harness offline").
"""

import pytest

from agentkit.telemetry import Tracer, bind_context, build_tree, percentile, span, summarize, use_tracer

def test_no_active_tracer_records_nothing():
    tracer = Tracer("r1")
    with span("task", "outside"):        # no use_tracer(...): a no-op
        pass
    assert tracer.spans == []


def test_nested_spans_get_parent_trace_and_dotted_order():
    tracer = Tracer("r1")
    with use_tracer(tracer):
        with span("task", "root"):
            with span("tool", "child"):
                pass
    root, child = tracer.spans
    assert root.parent_id is None and root.trace_id == root.span_id
    assert child.parent_id == root.span_id and child.trace_id == root.span_id
    assert child.dotted_order.startswith(root.dotted_order + ".")


def test_an_exception_marks_the_span_error_and_is_reraised_unchanged():
    tracer = Tracer("r1")
    boom = KeyError("missing")
    with use_tracer(tracer):
        with pytest.raises(KeyError) as err:
            with span("tool", "read"):
                raise boom
    assert err.value is boom
    assert tracer.spans[0].status == "error" and "KeyError" in tracer.spans[0].error

    with use_tracer(tracer):
        with pytest.raises(ValueError):
            with span("banana", "x"):
                pass


def test_percentile_is_nearest_rank_and_build_tree_nests_children():
    tens = list(range(10, 101, 10))
    assert (percentile(tens, 50), percentile(tens, 95), percentile([], 50)) == (50, 100, None)

    spans = [
        {"span_id": "a", "parent_id": None, "dotted_order": "1", "start_ms": 1.0},
        {"span_id": "c", "parent_id": "a", "dotted_order": "1.9", "start_ms": 3.0},
        {"span_id": "b", "parent_id": "a", "dotted_order": "1.1", "start_ms": 2.0},
        {"span_id": "o", "parent_id": "gone", "dotted_order": "9", "start_ms": 4.0},
    ]   # orphan
    roots = build_tree(spans)
    assert [r["span_id"] for r in roots] == ["a", "o"]                     # the orphan stays a root
    assert [c["span_id"] for c in roots[0]["children"]] == ["b", "c"]       # ordered by start

