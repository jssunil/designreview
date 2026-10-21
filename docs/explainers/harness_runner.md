# `harness/runner.py` and `harness/agent_tasks.py` — the official runner

*Reading order: **15th**. Files: `harness/runner.py`, `harness/agent_tasks.py`, `scripts/check_results_json.py`.
Config: `agentswitch-harness.toml`. Written in ASD-STE100 (Simplified Technical English) style, 2026-10-19.*

## Overview

The platform runs this module to score the team. The server clones the saved branch and installs
`requirements.txt`. Then it runs `python -m harness.runner` one time for each instance in
`agentswitch-harness.toml`, on a new copy of that instance. The module writes `results.json`. The server reads
that file and shows a pass or a fail for each entry.

The step that runs the agent has no internet access. All calls go through `AGENTSWITCH_BASE_URL` (the MCP
server) and `OPENAI_BASE_URL` (the LLM of the platform). The server discards all writes. The limits are 200
entries and 20 minutes.

| Part | File | What it writes to `results.json` |
|---|---|---|
| Smoke checks | `runner.py` | 9 entries: the platform and the seat work, and the seat still has what the agent needs. |
| Our agent | `agent_tasks.py` | One entry for each task, and one entry for each check that applied. |
| Format check | `scripts/check_results_json.py` | Nothing. It examines a `results.json` file against the specification. |

## Two modes

| Mode | Credentials | Command |
|---|---|---|
| Server | The server injects `AGENTSWITCH_BASE_URL`, `AGENTSWITCH_TOKEN`, `AGENTSWITCH_INSTANCE`, `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `OPENAI_MODEL`. | `python -m harness.runner` |
| Local | `.env`: the password login of the tenant. | `python -m harness.runner --instance keystone` |
| Offline | None. The captured fixtures replace the platform. Only the agent part runs. | `python -m harness.runner --instance suryodaya --sim packs/designreview/fixtures --out runs/r.json` |

**The token rule.** The runner uses `AGENTSWITCH_TOKEN` only when the process got it from outside, as on the
server. A token that is only a copy in `.env` expires. Before 2026-10-16, the runner used such a copy first,
and each platform check got a 401 after the copy expired. Now a local run always logs in with the password.
See `config.md` for `from_process()`.

## Part 1: the smoke checks

1. `mcp_tools_list`: the MCP handshake works and the seat gives its tool list.
2. `required_tools_present`: the seat has each tool that the pack needs (`REQUIRED_TOOLS` in
   `packs/designreview/actions.py`), with the arguments that the agent sends.
3. `required_tools_compatible`: each argument that the agent sends keeps its name and its type since the
   captured fixture, and no tool has a new necessary argument. Other schema changes, such as new optional
   filters, go into the evidence only.
4. `mcp_tool_call`: a direct call to `TeamHarness.get_mine` gives the saved repository link of the team.
5. `agent_answers_with_tool`: the LLM of the platform calls that tool in its own loop and answers with the
   link.
6. `design_file_retrieval`: a direct read of the target design file and its versions.
7. `release_readiness_gate`: a direct read of the release gate.
8. `refusal_unknown_entity`: the platform refuses a design file id that does not exist. An exception is a
   failure, not a refusal.
9. `agent_design_review_verdict`: the LLM of the platform gives a release verdict. The verdict must agree
   with the gate of check 7.

Check 9 reads the verdict with the negation-aware parser of the pack. Before 2026-10-16, it looked for
keywords. Thus "nothing is blocking, it is ready" passed, because it has the word "block". Now an answer that
says both "ready" and "not ready" fails.

## Part 2: our agent

`run_agent_tasks(instance, record)` in `agent_tasks.py` does these steps:

1. It selects the pack tasks for the instance: 8 on Suryodaya and 4 on Keystone.
2. It runs them as one `agentkit` batch. Each task runs in its own subprocess, as a dry run. The answer
   comes from the LLM of the platform when `OPENAI_API_KEY` is set. If not, the answer is the template.
3. The harness reads the ground truth before and after each run with its own login, and grades each run from
   its saved files.
4. It writes the entries:
   - `<instance>:task:<task id>`: the task passed or failed. The score is the partial credit. The evidence
     gives the failed checks, the source of the answer and the model.
   - `<instance>:check:<task id>:<check>:<name>`: one for each check that applied, golden values included.
     A failed check gives the expected value and the observed value.

## Failures are results

- A check that ends as `fail`, `error` or `drift` is `passed: false`.
- A task whose agent crashed, or whose files are missing, is `passed: false`.
- An exception in the module becomes one failed entry, with the exception and the file and line of its
  cause.
- A skipped check gets no entry: only a check that ran is a result.
- The runner exits 1 when any entry failed.

## The format check

`scripts/check_results_json.py` examines the file:

- the top level has `tasks` (1 to 200 entries) and `summary`;
- each entry has a unique `id`, a `title`, a strict boolean `passed`, a `score` from 0 to 1 and `evidence`.

It also applies our own rules:

- with `--instance`, each id starts with the instance name;
- a passed entry has the score 1.0;
- a failed entry has evidence;
- `--min-agent-tasks N` needs at least N task entries;
- `--expect all-pass` or `--expect some-fail` sets the expected result.

## In CI

CI runs the offline mode for both instances and checks each file with `--expect all-pass`. Then it plants a
fault (`fail_always:endpoint.designreview.release_readiness:flaky`). The runner must exit 1, and the file must
pass `--expect some-fail`. Thus CI shows that a failure reaches `results.json` with its evidence.

## Measured

On 2026-10-16, a live run gave 147 of 147 entries on Suryodaya (266 s) and 93 of 93 on Keystone (150 s). In
server mode, with an injected token and the OpenAI key, Suryodaya gave 147 of 147 in 193 s.

## How to test

Run `python -m pytest tests_local/test_server_runner.py -q`. These tests are offline. The hand-written template
is `tests/test_server_runner.py`.
