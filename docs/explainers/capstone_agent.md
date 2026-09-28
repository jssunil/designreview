# `capstone_agent.py` — the Design Review agent

*Reading order: **10th**. Wires `agentkit` + the design-review pack. Always a dry run.*

## 10,000-ft view

Two ways in:

```bash
python capstone_agent.py --task packs/designreview/tasks/t01_battery_tray_rev_diff.toml [--tenant keystone] [--skip-llm]
python capstone_agent.py            # the default T01 question, first-draft style (proofs/runs/)
```

`--task` runs exactly what the harness runs (`agentkit.harness.run_one.run_task`) into `runs/<run_id>/`.
Graded runs come from the batch (`capstone_evals.py`), which also captures ground truth.

## What `Team21DesignReviewAgent.run()` does

1. Writes `TaskRun(..., ended="running")` to `proofs/runs/` before any work.
2. Builds a `JournaledSeatClient` over the seat's MCP client (read-only endpoints declared by the pack),
   loads `plans/rev_diff_dfm.toml`, and runs it (`run_plan`): design file first; revisions, release gate,
   feedback and standards in parallel; a follow-up read of failed checklists if the gate reports blockers.
3. `record_steps()` copies each node's verdict into the run record.
4. `answer_with_pack()` builds the finding, asks the LLM (quality tier) to word it, audits the wording,
   rewrites once if needed, falls back to the template otherwise.
5. Records an unexpected exception as `ended="error"`; saves the record again at the end.

`mcp_client` (the raw-envelope `AgentSwitchClient`) is kept for scripts that call `call_mcp()` directly.

## Why it's written this way

The agent holds no domain logic of its own any more: the tool calls are pack actions, the plan and the
follow-up rule are TOML, the prompts are Markdown files in the pack, and the facts in the answer come from the
code-computed finding. What was once a hard-coded planner class and a synthesis prompt in this file is now data.

## How to test

`tests/test_agent_tasks.py` (live + agent). The offline twin: `python -m agentkit.harness.batch --sim
packs/designreview/fixtures/suryodaya.json --skip-llm --grade`.
