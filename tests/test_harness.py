"""
Offline tests for the run record and the harness grader (raw run first, grade later).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] TaskRun.open_in(run_dir) writes taskrun.json with ended="running" BEFORE any work
  [ ] A saved run loads back with the same task_id, prompt, steps, finding and claimed_answer
  [ ] A run that crashed after open_in() still has a record, and grades as "fail" (run_completed)
  [ ] add_step records kind / target / ok / detail (and status / error_kind) in order
  [ ] tool_calls_policy fails a dry run whose tool_journal.jsonl shows a write tool
  [ ] tool_calls_policy fails a write run that called a tool outside RunBundle.write_tools, passes one inside it
  [ ] run_task(..., write=True) stays a dry run unless the task sets allow_writes = true
  [ ] batch --write refuses --sim; --keep-writes needs --write
  [ ] Grading the same saved run folder twice gives identical score.json (deterministic)
  [ ] overall_status: any fail/error -> fail; only drift -> drift; else pass
Hint: use pytest's tmp_path as the run folder; write taskrun.json / tool_journal.jsonl /
ground_truth_after.json yourself, then call agentkit.harness.grade.grade_run(tmp_path).
"""

import pytest

from agentkit.harness.grade import grade_run, overall_status
from core.harness import Step, TaskRun

# Write your tests below.
