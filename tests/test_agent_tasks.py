"""
End-to-end agent tasks, verified against the DATABASE, not the agent's prose.
Markers: agent + live (module-wide). Costs LLM calls, so run sparingly.

Author(s): Sunil Jakkaraju    Written by hand: [yes] 

The tasks are data: packs/designreview/tasks/*.toml. Run one with
agentkit.harness.run_one.run_task(task, "suryodaya", tmp_path, "id") and assert on the
returned TaskRun (finding + claimed_answer) and the saved run folder.

Tasks to cover:
  [ ] t01 Battery Tray rev B -> rev C: finding maps B=v2, C=v3; answer cites 2.0 -> 3.0 mm and 4.2 mm;
      release gate NOT ready with 1 critical open; answer_source is "llm" and its audit is clean
  [ ] t02 unknown file: finding outcome "declined"; tool_journal.jsonl shows only DesignFile.get
  [ ] t03 AI review requested: declined because endpoint.designreview.ai_review is not in the seat;
      that tool never appears in tool_journal.jsonl
  [ ] t04 Bench Vice rev 2 -> rev 3: numeric revs mapped; answer mentions the 180 g saving;
      no sentence claims a geometry comparison
  [ ] t05 Propeller: rev B = v1 and rev C = v2 (mapped by commit note, not by position)
  [ ] Every run: taskrun.json existed with ended="running" before the plan ran (read it from a
      patched plan step, or check graph.json + taskrun.json timestamps)
  [ ] Keystone: the same question on a Keystone file answers from Keystone data (mark keystone)
Hint: the offline twin of every task is `python -m agentkit.harness.batch --sim
packs/designreview/fixtures/suryodaya.json --skip-llm --grade` -- use it to check your
expectations for free before spending LLM calls here.
"""

import pytest

from agentkit.harness.run_one import run_task
from agentkit.harness.tasks import load_task

pytestmark = [pytest.mark.agent, pytest.mark.live]

# Write your tests below.
