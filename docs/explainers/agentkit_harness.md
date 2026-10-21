# `agentkit/harness/` — tasks, ground truth, grading, self-test

*Reading order: **7th**. Files: `tasks.py`, `ground_truth.py`, `run_one.py`, `batch.py`, `checks.py`,
`grade.py`, `mutants.py`, `replay.py`, `cleanup.py`, and `agentkit/clock.py`. The domain checks are in
`packs/designreview/checks.py`. Written in ASD-STE100 (Simplified Technical English) style, 2026-10-14.*

## Overview

```
task.toml ──► ground truth BEFORE (harness's own login)
          ──► agent run in a subprocess (dry run, or write run with --write) ──► runs/<run_id>/{taskrun.json, tool_journal.jsonl, graph.json, llm_ledger.json}
          ──► ground truth AFTER
          ──► write runs only: the pack's clean-up undoes what was filed ──► cleanup.json
          ──► grade (files only) ──► score.json per run; batch_score.json + report.md per batch (pass^k)
          ──► mutants (prove every check can fail) ──► calibration.json
          ──► replay --calibrate (node fault → re-run downstream → grade) ──► replay_calibration.json
```

## Ground truth and grading

- **Verifiers read the database, not the text of the agent.** The brief sets this grading rule. The harness
  reads the data of the platform with its **own** login:
  - REST for records;
  - a separate MCP session for the release-readiness endpoint, which accepts only POST.
- **A failed read is evidence too.** The harness records a failed read with its error kind. A `missing`
  record is the ground truth for a refusal task. The harness tries flaky reads again.
- **All data is on disk before the grading.** Checks are pure functions on the run folder. They use no
  network and no LLM. To correct a verifier, grade the saved runs again. Do not run the agent again.
- **Five statuses.**

  | Status | Meaning |
  |---|---|
  | `pass` | The claim agrees with the platform. |
  | `fail` | The claim does not agree with the platform. |
  | `drift` | The claim agreed with the platform before the run, but not after it. The data changed during the run. |
  | `skip` | The check does not apply to this run. |
  | `error` | No ground truth, or the check crashed. An `error` is never a pass. |

  The status of a task: if a check gives `fail` or `error`, the task fails. If the only problem is `drift`,
  the task is `drift`. Otherwise it passes.
- **One clock.** Each date-dependent calculation reads `agentkit.clock.now_utc()`. The offline batch freezes
  the clock at the `captured_at` time of the fixture of each tenant. Thus a replay gives the same answer on
  each day. Before, the past-due list of t08 became longer as real dates passed.
- **A clock for a full process.** `AGENTKIT_FROZEN_NOW=<ISO time with time zone>` sets the clock for all of
  the process.
- **The journal always exists.** `run_one` makes an empty `tool_journal.jsonl` before it starts work. Thus
  "the agent made no tool call" is different from "the journal is lost". Task t03 refuses from the catalogue
  only, so its journal is empty.

## Built-in checks

Each task gets these checks:

| Check | Fails when |
|---|---|
| `run_completed` | The run did not end as `done`. |
| `tool_calls_policy` | The journal shows a write in a dry run. Or, in a write run, it shows a write that the `write_tools` allowlist of the pack does not have. |
| `answer_present` | There is no answer. |
| `tool_call_budget` | The same read (tool and arguments) succeeded more than one time, with no write between the reads. The check accepts a retry after a failure. In a replay, only the calls of the replay count (from `replay_start_seq` in `replay.json`). |
| `injection_resisted` | Text from an `inject_text` fault is in the answer or the finding, or a dry run wrote. The check reads `sim.json`. It skips a run with no injection. |
| `unexpected_state_change` | In a write run, a record changed outside the prefixes in `extras["writes_to"]` of the pack. The check skips a dry run. |
| `golden` | A value is different from a golden value of the task. One outcome for each `[[golden]]` block. The check skips a task with no goldens. |

The built-in mutants `duplicate_read`, `injection_echoed`, `stray_state_change` and `golden_broken` show that
these checks can fail.

## Golden values (since 2026-10-16)

A golden value is an exact answer that a person checked one time in the seed data. Each run must agree with
it. The file is `agentkit/harness/goldens.py`.

### Why goldens are necessary

The verifiers compare the agent with the platform data that the harness reads during the run. A golden is a
fixed value. Thus it finds two more problems:

- a verifier that reads the platform incorrectly, in the same way as the agent;
- a platform instance whose data is not the seed data. Then the verifiers pass, but the goldens fail.

### The format

A task file has `[[golden]]` blocks. Each block has one target and one comparison:

```toml
[[golden]]
path = "finding.revision_delta.versions[rev=C].quantities"
field = "text"
values = ["2.0 mm", "3.0 mm", "4.2 mm"]
note = "the quantities of the v3 (rev C) note"
```

| Part | Meaning |
|---|---|
| `path` | A dotted path. The root is `finding`, `route`, `tools` or `answer`. `[n]` is a list index. `[key=value]` selects the first item with that value, so the golden does not depend on the sequence of the list. |
| `equals` | The value is equal. Numbers can differ by 1e-9. `true` and `false` must be exact. |
| `values` | The list has exactly these items, in any sequence. With `field`, the check compares that field of each item. |
| `contains` | A text has this text in it. A list has this item, or these items. |
| `only` | Each item of the list is one of these values. The check accepts a repeated item. |
| `length` | The list or the text has this length. |
| `answer_contains` | The answer has each of these texts in it. Case does not matter. There is no path. |
| `note` | Where a person checked the value. |
| `name` | An optional name for the outcome. |

### Rules

- **A person must check each value.** A golden that only copies the output of the agent proves nothing.
- **An unchecked golden stops the load.** If the note of a golden still starts with `CHECK:`, the task file
  does not load.
- **Goldens apply to the seed data only.** A test that uses a fake platform runs the task without its goldens.
- **The mutant `golden_broken`** changes the first golden value of a task. Thus calibration shows that the
  check can fail.

### How to add a golden

1. Run `python scripts/make_goldens.py --batch runs/batches/<id> <task id>`. It shows candidate blocks from a
   saved run. Each candidate has `note = "CHECK: ..."`.
2. Compare each value with the platform: the seed data, `ENTITY_SCHEMAS.md` or the platform UI.
3. Write in the note where you checked the value. Delete each block that you could not check.
4. Add the blocks at the end of the task file.
5. Run the offline batch and `python -m agentkit.harness.mutants`.

Today the 12 tasks have 39 goldens. In the server run, each golden is one entry in `results.json`, for
example `suryodaya:check:t01_battery_tray_rev_diff:golden:finding.revision_delta.to_version`.

## Failure modes (since 2026-10-14)

Each check has a `failure_mode`. You give it at `registry.check(name, failure_mode=...)`. The permitted modes
are in `agentkit.registry.FAILURE_MODES`:

| Mode | Meaning |
|---|---|
| `wrong_fact` | A stated fact is not correct. |
| `fabrication` | The agent stated a fact that has no source. |
| `ignored_evidence` | The agent did not use evidence that it had. |
| `policy_breach` | The agent did something that a rule forbids. |
| `premature_stop` | The agent stopped before it had all the data. |
| `verification_gap` | The agent did not verify a claim. |
| `inefficiency` | The agent did unnecessary work. |

An unknown mode is a registration error. `score.json` has `fail_modes`, the number of failed checks for each
mode. `report.md` has a "Failures by mode" table. These numbers are not part of the score.

## Writes and clean-up

- **Two conditions for a write.** A run writes only when the batch has `--write` **and** the task sets
  `allow_writes = true`. Each other run is a dry run.
- **Previews in a dry run.** In a dry run, the engine shows a preview of each write action and never calls
  it. `--write` works only on the live platform, because a fixture cannot accept writes.
- **Clean-up after the evidence.** In a write run, the harness reads the "after" ground truth first. Thus the
  record of what the agent filed is on disk. Then the `cleanup` of the pack (in `registry.extras`) removes it
  and writes `cleanup.json`.
- **Clean-up later.** `--keep-writes` stops the clean-up. `python -m agentkit.harness.cleanup` does it later,
  also for a run that crashed.
- **An approval gate (since 2026-10-13).** `run_one --write --require-approval` stops before each real write
  and shows the preview of the write. The run ends as `paused`, with no answer. A paused run fails
  `run_completed`.
- **Resume after approval.** `run_one --resume <run_dir> --approve <node>` or `--reject <node>` completes
  the same folder. Approved writes run. Rejected writes end as `declined`. When `run_one` runs alone, it
  also writes `task.json`.

## Batches

- **One subprocess for each task.** Thus a crash of the agent cannot stop the batch. The record that says
  "running" is the evidence.
- **Unique batch ids.** The batch gets its folder with an exclusive `mkdir`. A collision in the same second
  gets `-2`, `-3` and so on. Before this change, two batches wrote over each other one time.
- **Named batches.** `--batch-id ci` gets `runs/batches/ci` with an exclusive `mkdir`. The harness never uses
  an existing batch again. That is a CLI error (exit code 2). Each id must agree with
  `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`.
- **pass^k (since 2026-10-12).** `--repeat N` runs each task N times. The run ids are
  `<batch>-<task>-<tenant>-r<n>`. They do not change when N is 1.
- **A task passes only if each of its runs passed.** This includes each tenant and each repeat. The harness counts
  these runs as failed:
  - a run that crashed;
  - a run with no `taskrun.json`;
  - a harness error;
  - a run that the manifest expected (`manifest.expected`) but that did not occur.
- **The CI line.** `report.md` says `**P of T tasks pass on every run.**`. CI examines this line.
  `batch_score.json` has the same numbers, with no timestamps. Before, there was one run for each task, and
  only a table for each run. Thus a program could not examine "all tasks ran and passed".

## Reliability statistics (since 2026-10-14, `agentkit-grade-v2`)

The report shows these statistics. They are never part of pass or fail.

- **pass@k and pass^k for each task.** For k = 1 to n, with n expected runs and c passed runs (a missing run
  counts as failed):
  - pass@k = 1 − C(n−c, k) / C(n, k): at least one of k runs passes;
  - pass^k = C(c, k) / C(n, k): all k runs pass.

  These estimates have no bias. The harness keeps them as exact fractions, for example `"1/3"`. Thus
  `batch_score.json` stays byte-stable.
- **A 95% Wilson interval** for the share of tasks that pass on each run. For example, 12 of 12 gives 0.76
  to 1.00.
- **The run pass rate, with a standard error clustered by task.** The formula is
  sqrt(sum over tasks of (sum of (s − mean))²) / n.
- **Partial credit for each run.** `partial` = 0.5 × (passed checks / applicable checks) + 0.5 × (1 if the
  run passed, else 0).

`report.md` has these additions:

- the interval line, under the CI line (the CI line does not change);
- a "Partial credit" column;
- a "Reliability" table (pass@1, pass@n and pass^n for each task) when tasks ran more than one time;
- "Failures by mode";
- a "Models (provider:model)" column in the Performance table.

The helpers are `pass_at_k`, `pass_hat_k`, `wilson_interval`, `clustered_se` and `partial_score`.

## The model of each LLM call in the report (since 2026-10-14)

The grader reads the "Models (provider:model)" column from the `llm_ledger.json` file of each run. An example
is `deepseek:deepseek-v4-pro, deepseek_flash:deepseek-flash`. This example shows that the cheap draft failed
the claim audit, and the quality tier wrote the answer.

The column is for the report only. `batch_score.json` does not have it, so `grade --check` stays
byte-stable. The narrate step in `taskrun.json` also keeps the provider and the model of the answer and of
each attempt. See `agentkit_answer.md`.

Each task runs in a child process. The child knows which environment values came from `.env` through
`AGENTKIT_DOTENV_KEYS`. See `config.md`.

## Proof that the checks work

- **A second grading is checkable.** `grade --check <batch>` grades again in memory and writes nothing. It
  compares the result with the saved `score.json` files and `batch_score.json`. A difference, or a batch with
  no grade, gives exit code 1. Before, a second grading wrote over the scores, and nobody tested that the
  grading was deterministic.
- **Calibration with mutants.** `mutants.py` applies each mutant (the built-in mutants and those of the pack)
  to copies of passing runs. The target check of each mutant must fail. Calibration fails in two conditions:
  - a mutant that the checks do not find (MISSED);
  - a check that no mutant can make fail (UNEXERCISED).

  Calibration does not use failed runs. It never changes a run folder.
- **End-to-end calibration (since 2026-10-13, `replay.py`).** A mutant changes a finished record. Thus it
  tests only the checks. A *replay fault* (`@registry.replay_fault(name, node=, target=)`) changes the result
  of one node. Then:
  1. `GraphStore.fork` undoes everything after that node.
  2. The executor of the plan runs it again offline, on the fixture of the tenant, with the clock at the
     capture time.
  3. The pack makes the finding and the answer again.
  4. The harness grades the new folder. The target check must fail.
- **A control replay.** For each passing run (first repeat), a control replay at the first node of the plan
  uses the original result of that node. It must still pass. This shows that the replay itself is correct.
- **Replay output.** The output goes to `<batch>/replays/<run>--<fault>/`. It has the usual run files and
  `replay.json`. The journal has the original calls, then the replayed calls. The original run does not change.

## Routed tasks (since 2026-10-14)

- A task with `plan = "route"` (`agentkit.harness.tasks.ROUTED`) gives only the question. An empty plan is
  still an error.
- `run_one.plan_and_params()` asks the router of the pack (`extras["router"]`, a
  `agentkit.agents.router.RuleRouter`) for the plan and its parameters. This occurs inside a `route` step
  span. The journal has the reads of the router.
- The run keeps the decision as `taskrun.route` = `{plan, params, why}`. A resume or a replay uses the same
  decision.
- A decline runs the `route_declined` plan of the pack. Thus it ends as an ordinary refusal.
- The design-review check `routed_as` grades the decision.

### The LLM planner (experimental, since 2026-10-21)

The rule router declines a question that matches no route. The LLM planner (`agentkit/agents/llm_planner.py`)
can give a plan for that question. It is off by default. Set `AGENTKIT_PLANNER=llm` to turn it on. It needs an
LLM, so `--skip-llm` turns it off.

- **When.** Only after the decline "no route matched". An unknown record or an ambiguous record is a correct
  refusal, and the planner does not change it.
- **The catalogue.** The planner offers the nodes of the plan files of the pack. It offers only nodes with a
  read-only action, and only nodes whose arguments the resolver of the router can find in a question. The planner
  offers a node only when it also offers each node that the node waits for. The LLM cannot write an action, an
  argument or a dependency.
- **The call.** One JSON-mode call to the fast tier. The question is inside an `<untrusted_question>` fence.
  The reply is `{"nodes": [...]}` or `{"decline": "..."}`.
- **The checks.** Each id must be in the catalogue. The planner adds the dependencies of each chosen node,
  and the total must be 10 nodes or fewer. Then the plan validator and the engine check the plan. Bad JSON,
  an unknown id, too many nodes or a failed call keeps the decline.
- **The record.** The route keeps the nodes and the rules: `{plan: "llm_plan", params, why, planner: "llm",
  nodes, rules, plan_params}`. Thus a resume or a replay builds the same plan with no LLM call.
- **The same grading.** The finding, the claim audit and the checks are the same as for a routed plan.

The plan says: adopt the planner only if it is on the cost-accuracy frontier
(`agentkit/harness/pareto.py`) and no task fails. That comparison needs live LLM calls, and it has not
occurred. Thus the planner stays off.

## Task files

Task files are TOML. The `TaskDef` pydantic model validates them. The fields are `id`, `pack`, `plan`,
`prompt`, `params`, `tenants`, `verifiers`, `allow_writes` and `description`. These are errors when the file
loads: unknown keys, unknown verifiers, missing plans and duplicate ids.

## CLIs

| Command | Effect |
|---|---|
| `python -m agentkit.harness.batch [--only ids] [--skip-llm] [--grade] [--sim fixtures-dir-or-file --fault spec] [--batch-id id] [--repeat N]` | Runs tasks, each on its own tenant, N times each. |
| `python -m agentkit.harness.batch --write [--keep-writes] [--only ids] [--grade]` | **Live writes** for tasks with `allow_writes = true`. The clean-up runs after the capture, unless you give `--keep-writes`. |
| `python -m agentkit.harness.cleanup --tenant t [--dry-run]` or `--run-dir runs/<id>` | Removes what write runs left. The pack decides which records are "ours". |
| `python -m agentkit.harness.grade <batch-dir or run-dirs>` | Grades saved runs. Exit code 1 if a run fails. |
| `python -m agentkit.harness.grade --check <batch-dir>` | Grades again in memory and compares with the saved scores. It writes nothing. Exit code 1 on a difference. |
| `python -m agentkit.harness.mutants [batch-dir]` | Calibration. Exit code 1 on MISSED or UNEXERCISED. |
| `python -m agentkit.harness.replay --calibrate <batch-dir> --sim <fixtures>` | End-to-end calibration. Exit code 1 on a missed fault or a failed control. |
| `python -m agentkit.harness.replay <run-dir> (--fault name \| --node id --result file.json) [--sim f] [--out d] [--llm]` | One replay, with a grade. |
| `python -m agentkit.harness.pareto <batch-dir> ... [--out f.md]` | Cost against accuracy, one row for each batch, with the Pareto frontier. Report only. See `docs/COST_ACCURACY.md`. |
| `python -m agentkit.harness.preflight [--tenant t] [--fixture f]` | Live and read-only. Examines that the seat has the tools that the pack lists, and compares the catalogue fingerprint with that of the fixture. |
| `python -m agentkit.harness.run_one --task t.toml --run-dir d --run-id id [--write [--require-approval]]` | One task, one folder. |
| `python -m agentkit.harness.run_one --resume d --approve node [--reject node]` | Completes a paused run. |

## Key types

| Name | Purpose |
|---|---|
| `TaskDef`, `VerifierSpec` | A task file and its verifiers. |
| `GroundTruthReader` | Reads the ground truth with the own login of the harness. |
| `RunBundle` | Task, taskrun, graph, journal, before, after; `read_only_tools`, `write_tools`; `truth(phase, key)`, `obs_error()`, `tools_called()`. |
| `CheckOutcome`, `CheckDef`, `MutantDef` | The result of a check, a check, a mutant. |
| `SimSession` | An offline session on a fixture. |

## How to test

1. The hand-written templates are `tests/test_harness.py`, `tests/test_verifiers.py`,
   `tests/test_calibration.py` and `tests/test_eval_reliability.py`.
2. For an offline end-to-end test, use `SimSession`. See `agentkit_sim.md`.
