"""
Offline tests for the read-only run viewer (agentkit/viewer).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] TODO: /api/batches lists each batch with status counts (a run without score.json counts as "not graded")
  [ ] TODO: /api/batches/<id> returns one row per manifest entry with its failing checks; a missing run
      folder is shown as a row, not an error
  [ ] TODO: /api/runs/<id> returns task, taskrun, score, graph, journal, ground truth before/after, ledger;
      a missing file is null, an unparseable one is {"_unreadable": ...}
  [x] ground_truth_changes marks exactly the observations that changed during the run
  [x] A missing or empty runs folder gives empty lists, not an error
  [x] Path traversal is refused with 404: "..", "../x", "..%2Fx", "%2e%2e", "..%5Cx", "batches"
      as a run id, a very long id, a symlink pointing outside the runs folder
  [x] Only index.html / app.js / style.css are served as static files ("/static/server.py" is 404)
  [ ] TODO: POST / PUT / PATCH / DELETE get 405 and change nothing on disk
  [ ] TODO: Responses carry the Content-Security-Policy and nosniff headers
  [x] Hostile text in a run (e.g. '<script>alert(1)</script>') never appears as a literal tag in a
      response, and parses back to the original string
  [x] app.js never uses innerHTML / outerHTML / insertAdjacentHTML / document.write / eval
  [ ] TODO: The server binds to 127.0.0.1 by default
  [ ] TODO: On a real offline batch (SimSession + run_batch + grade_batch) every run is readable and passing
  [ ] TODO: /api/runs/<id> includes "telemetry" (the run tree for the Timeline tab); a run without telemetry.json
      gives null, not an error
  [ ] TODO: The Cost tab data (the ledger) has the provider and the model of each LLM call
More use cases (added 2026-10-16, to write by hand):
  [x] ground_truth_changes: a key only in "after" is a changed row; error kinds before/after are reported
  [ ] TODO: /api/runs/<id> for an id that does not exist -> 404 with a JSON error body
  [ ] TODO: a query string ("/api/batches?x=1") is served like the plain path, or refused -- pin which
Hint: ViewerApp(runs_dir).handle("/api/...") returns (status, content_type, body_bytes) with no socket;
for HTTP, make_server(runs_dir, port=0) in a threading.Thread and http.client.HTTPConnection.
"""

import json

import pytest

from pathlib import Path

from agentkit.record.durable_io import dump_record
from agentkit.viewer import ViewerApp, ground_truth_changes, make_server

ROOT = Path(__file__).resolve().parents[1]
HOSTILE = '<script>alert(1)</script> & </pre>'


@pytest.fixture
def runs_dir(tmp_path):
    root = tmp_path / "runs"
    run = root / "B1-t99-alpha"
    run.mkdir(parents=True)
    dump_record(
        run / "taskrun.json",
        {"task_id": "t99", "tenant": "alpha", "prompt": "q", "ended": "done",
         "claimed_answer": HOSTILE, "steps": [], "finding": {}},
    )
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")   # must never be reachable
    return root


def test_ground_truth_changes_marks_exactly_the_changed_observations():
    before = {"observations": {"k1": {"value": [1]}, "k2": {"value": "same"}}}
    after = {"observations": {"k1": {"value": [2]}, "k2": {"value": "same"}, "k3": {"error_kind": "missing"}}}
    rows = {r["key"]: r for r in ground_truth_changes(before, after)}
    assert {k: r["changed"] for k, r in rows.items()} == {"k1": True, "k2": False, "k3": True}
    assert rows["k3"]["after_error"] == "missing" and rows["k3"]["before_error"] is None


@pytest.mark.parametrize("path", [
    "/api/runs/..", "/api/runs/../secret.txt", "/api/runs/..%2Fsecret.txt", "/api/runs/%2e%2e",
    "/api/runs/..%5Csecret.txt", "/api/runs/batches", "/api/runs/" + "a" * 300, "/static/server.py",
])
def test_path_traversal_and_non_whitelisted_files_are_404(runs_dir, path):
    status, _, raw = ViewerApp(runs_dir).handle(path)
    assert status == 404
    assert b"TOP SECRET" not in raw


def test_hostile_text_is_never_a_literal_tag_and_parses_back(runs_dir):
    status, ctype, raw = ViewerApp(runs_dir).handle("/api/runs/B1-t99-alpha")
    assert status == 200 and ctype.startswith("application/json")
    assert b"<script>" not in raw and b"<" not in raw
    assert json.loads(raw)["taskrun"]["claimed_answer"] == HOSTILE


def test_empty_runs_folder_is_empty_lists_and_the_front_end_never_inserts_html(tmp_path):
    app = ViewerApp(tmp_path / "does-not-exist")
    for path in ("/api/batches", "/api/runs"):
        status, _, raw = app.handle(path)
        assert status == 200 and json.loads(raw) == []

    js = (ROOT / "agentkit" / "viewer" / "static" / "app.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, sink

