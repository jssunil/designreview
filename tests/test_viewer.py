"""
Offline tests for the read-only run viewer (agentkit/viewer).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] /api/batches lists each batch with status counts (a run without score.json counts as "not graded")
  [ ] /api/batches/<id> returns one row per manifest entry with its failing checks; a missing run
      folder is shown as a row, not an error
  [ ] /api/runs/<id> returns task, taskrun, score, graph, journal, ground truth before/after, ledger;
      a missing file is null, an unparseable one is {"_unreadable": ...}
  [ ] ground_truth_changes marks exactly the observations that changed during the run
  [ ] A missing or empty runs folder gives empty lists, not an error
  [ ] Path traversal is refused with 404: "..", "../x", "..%2Fx", "%2e%2e", "..%5Cx", "batches"
      as a run id, a very long id, a symlink pointing outside the runs folder
  [ ] Only index.html / app.js / style.css are served as static files ("/static/server.py" is 404)
  [ ] POST / PUT / PATCH / DELETE get 405 and change nothing on disk
  [ ] Responses carry the Content-Security-Policy and nosniff headers
  [ ] Hostile text in a run (e.g. '<script>alert(1)</script>') never appears as a literal tag in a
      response, and parses back to the original string
  [ ] app.js never uses innerHTML / outerHTML / insertAdjacentHTML / document.write / eval
  [ ] The server binds to 127.0.0.1 by default
  [ ] On a real offline batch (SimSession + run_batch + grade_batch) every run is readable and passing
Hint: ViewerApp(runs_dir).handle("/api/...") returns (status, content_type, body_bytes) with no socket;
for HTTP, make_server(runs_dir, port=0) in a threading.Thread and http.client.HTTPConnection.
"""

import json

import pytest

from agentkit.viewer import ViewerApp, ground_truth_changes, make_server

# Write your tests below.
