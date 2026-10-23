# `capstone_agent.py` — the Design Review agent

*Reading order: **13th**. This file connects `agentkit` and the design-review pack. It always does a dry run.
Written in ASD-STE100 (Simplified Technical English) style, 2026-10-14.*

## Overview

There are two ways to start the agent:

```bash
python capstone_agent.py --task packs/designreview/tasks/t01_battery_tray_rev_diff.toml [--tenant keystone] [--skip-llm]
python capstone_agent.py            # the default T01 question, first-draft style (proofs/runs/)
```

With `--task`, the agent does the same run as the harness (`agentkit.harness.run_one.run_task`). It writes the
run to `runs/<run_id>/`.

The graded runs come from the batch (`capstone_evals.py`). The batch also reads the ground truth.

## What `Team21DesignReviewAgent.run()` does

1. It writes `TaskRun(..., ended="running")` to `proofs/runs/` before it starts work.
2. It makes a `JournaledSeatClient` on the MCP client of the seat. The pack declares the read-only endpoints.
3. It loads `plans/rev_diff_dfm.toml` and runs it (`run_plan`):
   - first, the design file;
   - then, in parallel: the revisions, the release gate, the feedback and the standards;
   - if the gate shows blockers: one more read of the failed checklists.
4. `record_steps()` copies the result of each node into the run record.
5. `answer_with_pack()` makes the finding. Then the LLM tiers of the pack (fast, then quality) write it as
   text. The claim audit examines each draft. If no draft passes, the template gives the answer.
6. If an unexpected exception occurs, the agent records `ended="error"`. At the end, it saves the record again.

The agent keeps `mcp_client` (the raw-envelope `AgentSwitchClient`) for scripts that call `call_mcp()`.

## Design decision: no domain logic in the agent

This file has no domain logic now:

- the tool calls are pack actions;
- the plan and the follow-up rule are TOML files;
- the prompts are Markdown files in the pack;
- the facts in the answer come from the finding, which code calculates.

Before, this file had a planner class and a synthesis prompt in the code. Now these are data.

## How to test

- Live: `tests/test_agent_tasks.py` (markers `live` and `agent`).
- Offline: `python -m agentkit.harness.batch --sim packs/designreview/fixtures --skip-llm --grade`.
