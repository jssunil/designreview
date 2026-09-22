# Test Plan — Team 21 Design Review Harness

> **Scope**: `as_client.py`, `core/` (gateway, DAG engine, harness, eval framework, judge), `capstone_agent.py`, `capstone_evals.py`, `rescore.py`, and the entity/workflow surface documented in `ENTITY_SCHEMAS.md`.
> **Companion reading**: [`docs/EXPLAINERS.md`](docs/EXPLAINERS.md) explains what each file does and why; this document specifies what to *test* and is organized by test type (functional / edge case / performance), not by file.

---

## 0. How to use this document — and the one rule that overrides everything else in it

**Per the assignment brief (`agentswitch.md` §8, "What we are grading"): "Tests you wrote by hand... A test written by Claude or Codex scores zero."**

This document is a **test specification**, not test code. Every test case below is written as an ID, an intent, and a pass/fail oracle — precise enough to implement without ambiguity, but the implementation (the actual `def test_...(): assert ...` you submit) must be typed by you. Treat this the way you'd treat a QA lead's test-case spreadsheet handed to an engineer: the thinking about *what* to test and *why* is done; the *how* — actually writing the assertions, choosing the mocking approach, wiring up fixtures — is the hand-written part that earns points.

Where this document gives example code, it is illustrative pseudocode to clarify intent, not a drop-in implementation — several examples are deliberately incomplete (`...`) for exactly this reason.

---

## 1. What we are actually grading, and where it shows up in this plan

Direct quotes from `agentswitch.md` §8, mapped to where this plan addresses them:

| Grading criterion (verbatim) | Addressed in |
|---|---|
| "The agent, answering your seat's questions against live data that other teams are changing underneath it." | §4.6 (agent functional tests), §5.3 (staleness/concurrency edge cases) |
| "Your own loop, a task set with verifiers that read the database rather than your agent's prose" | §4.7 (ground-truth axis tests) — this is the whole point of `core/eval_framework.py`'s design; §9 explains why a prose-keyword test *doesn't* count as satisfying this bar |
| "every run written to disk before anything is scored" | §4.4 (harness record tests), §4.10 (E2E: assert a `proofs/runs/*.json` file exists after every run, written before scoring runs) |
| "At least one task where the correct answer is refusal... An agent that invents a confident answer has failed that task however well it handled the others." | §4.10 (E2E-02), §5.2 (refusal / insufficient-authority edge cases) — this is the single highest-stakes test category in the whole plan |
| "Tests you wrote by hand" | §0 (this section) and every test ID below is scoped to be independently implementable by a human in well under the time it'd take to review a generated one |
| "a hundred points for every real bug you find in AgentSwitch" | §5 (the edge case suite) is written to double as a bug-hunting checklist — several edge cases below are phrased as "does the platform behave correctly when X," not just "does our agent behave correctly" |

---

## 2. Test strategy overview

```
                     ┌───────────────────────────────┐
  Tier 4: Live E2E   │  Full agent run against live    │  slow, costs money,
  (manual/nightly)   │  Suryodaya; refusal task;        │  non-deterministic
                     │  full harness → eval → rescore   │  wall-clock time
                     └───────────────┬───────────────┘
                                     │
                     ┌───────────────▼───────────────┐
  Tier 3: Component  │  Real core/ modules, FAKE       │  fast, deterministic,
  integration        │  AgentSwitchClient/LLMGateway    │  no network
  (CI, every push)   │  (recorded-response fixtures)    │
                     └───────────────┬───────────────┘
                                     │
                     ┌───────────────▼───────────────┐
  Tier 2: Unit        │  One class/function at a time,  │  fastest, run
  (CI, every push)   │  pure logic, no I/O at all       │  hundreds per second
                     └───────────────┬───────────────┘
                                     │
                     ┌───────────────▼───────────────┐
  Tier 1: Static      │  Schema-contract checks: does    │  catches the two real
  contract checks     │  every tool_args dict this repo  │  bugs found this repo
  (CI, every push)   │  builds satisfy a captured real   │  already hit (wrong
                     │  MCP inputSchema fixture?         │  tool names, wrong
                     └───────────────────────────────┘  field names)
```

**Recommended tooling**: `pytest` + `pytest-mock` (or bare `unittest.mock`) for Tiers 1-3; a `requires_live_api` custom marker (registered in `pyproject.toml`/`pytest.ini`) for anything in Tier 4 so `pytest -m "not requires_live_api"` is the default CI command and the live suite is opt-in (`pytest -m requires_live_api`). No `pytest-asyncio` is needed anywhere — nothing in this codebase is `async`.

**Why four tiers, not two.** The codebase splits cleanly into pure logic (`core/dag_engine.py`, `core/harness.py`, `core/eval_framework.py`, most of `core/economics.py`) and I/O-heavy glue (`as_client.py`, the provider adapters in `core/llm_gateway.py`, `capstone_agent.py`'s MCP calls). Tier 2 covers the former with zero mocking needed at all. Tier 3 covers the latter with fakes standing in for the network. Tier 1 is specifically here because **two real bugs in this project were caused by trusting an assumed MCP schema instead of the live one** — a dedicated contract-check tier makes that class of bug structurally harder to reintroduce.

---

## 3. Test data & fixture strategy

This is worth deciding before writing any test, because the live platform is a **shared, mutable, mostly-noisy** dataset (per `PLAN.md` §8/§9 and `SESSION_NOTES.md` §1.1: 100 seeded `DesignFile`/`DesignProject`/`DesignChecklist`/`DesignStandard`/`DesignFeedback` rows, but only ~9 files have any version history and only 5 reviews are coherent; text fields are often randomly-shuffled noise). Two consequences:

1. **Don't seed new test data into the shared platform casually.** Every write is attributed to your login and visible to anyone else on the seat (per `agentswitch.md` — "live data that other teams are changing underneath it" cuts both ways: your test fixtures would be underneath *them*, too). Prefer read-only tests against the one known-good real anchor (`DF-2026-00001`, the Bharat EV Battery Tray, `39b69109-b56a-4bf4-af48-1ecf2b18f8a6`) for anything that needs live data at all.
2. **Most edge cases below (zero-version file, single-version file, a `ready: true` file, a malformed field) don't exist in a convenient form in the live seed data.** Two honest options, in order of preference:
   - **(a) Record-and-replay fixtures.** Once, manually, capture a real MCP response for each scenario you need (a real `tools/list` schema, a real `DesignVersion.list` response, a real `release_readiness` response) and save it as a JSON fixture under `tests/fixtures/`. Tests then construct a fake `AgentSwitchClient`/`LLMGateway` that replays these fixtures — deterministic, offline, fast, and *grounded in a real response shape* rather than a guessed one (exactly the discipline that would have caught this project's two live-discovered bugs earlier).
   - **(b) Hand-construct synthetic fixtures** for scenarios that don't exist in live data at all (e.g. a `release_readiness` response with `ready: true` and zero blockers — every real file discovered so far is `ready: false`). These are invented, not captured, so label them clearly (e.g. `tests/fixtures/synthetic_ready_true_readiness.json`) so a future reader doesn't mistake them for a real platform response.
3. **Never commit real credentials, tokens, or bearer headers into a fixture file.** Scrub any captured response of `Authorization` headers before saving; the response *bodies* used in this plan don't contain secrets, only IDs and business content.

**Recommended fixture inventory** (capture these once, live, read-only, before writing Tier 1/3 tests):

| Fixture file | Captured from | Used by |
|---|---|---|
| `tools_list_designreview_seat.json` | `tools/list` (full catalogue) | Tier 1 contract checks (§4.9) |
| `design_file_get_battery_tray.json` | `DesignFile.get` on the real anchor | Tier 3 agent tests |
| `design_version_list_battery_tray.json` | `DesignVersion.list` on the real anchor (the 3-version rev A/B/C chain) | Tier 3 agent + eval-axis tests |
| `release_readiness_battery_tray.json` | `endpoint.designreview.release_readiness` on the real anchor | Tier 3 agent + eval-axis tests |
| `design_feedback_list_battery_tray.json` | `DesignFeedback.list` filtered to the real anchor | Tier 3 agent tests |
| `design_checklist_result_list_fail.json` | `DesignChecklistResult.list` with `overall_result: "fail"` | Tier 3 planner tests |
| `synthetic_single_version_file.json` | hand-constructed | Edge case E-DS-01 |
| `synthetic_zero_version_file.json` | hand-constructed (empty `data: []`) | Edge case E-DS-02 |
| `synthetic_ready_true_readiness.json` | hand-constructed | Edge case E-DS-04, functional F-AX-05 |
| `synthetic_jsonrpc_error_envelope.json` | hand-constructed (matches the real `-32602`/`tool_not_available` shapes already seen live) | Tests F-TR-09, F-AG-06 |
| `synthetic_needs_review_checklist.json` | hand-constructed (`overall_result: "needs_review"`) | Edge case E-DS-06 |

---

## 4. Functional test suite

### 4.1 Transport layer (`as_client.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-TR-01 | `get_environment_config("suryodaya")` returns the right URL/email when env vars are set | Unit | Returned dict matches expected keys/values |
| F-TR-02 | `get_environment_config("unknown")` raises `ValueError` | Unit | Exception raised, message names the bad env |
| F-TR-03 | `get_environment_config` raises `ValueError` when the tenant's password env var is unset | Unit | Exception raised, message names the missing var |
| F-TR-04 | `.login()` on success stores the returned token on `self.token` | Component (fake `urlopen`) | `client.token == "the-returned-token"` |
| F-TR-05 | `.login()` on an HTTP error surfaces the real response body in the raised `RuntimeError`, not a generic message | Component | Exception message contains the fixture's real error text |
| F-TR-06 | `.request()` auto-logs-in when `self.token` is `None` | Component | The fake login endpoint is hit exactly once before the requested endpoint |
| F-TR-07 | `.request()` does **not** re-login on a second call once a token exists | Component | Login endpoint hit exactly once across two `.request()` calls |
| F-TR-08 | `.call_mcp()` builds the exact JSON-RPC envelope (`jsonrpc`, `id`, `method`, `params`) | Unit/Component | Captured request body matches expected shape byte-for-byte |
| F-TR-09 | A JSON-RPC **error** response (HTTP 200, `{"error": {...}}` body) is returned as-is by `.call_mcp()` — it is the caller's job to detect it, and this test proves `.call_mcp()` does *not* raise or hide it | Component | Returned dict has `"error"` key; no exception raised |
| F-TR-10 | `.init_mcp()` calls `initialize` then `notifications/initialized`, in that order | Component | Two calls recorded, in the right order, with the right method names |

### 4.2 LLM Gateway (`core/llm_gateway.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-GW-01 | A provider with no API key set is skipped, not attempted | Unit | The corresponding adapter's mock is never called |
| F-GW-02 | The candidate order for `tier="fast"` matches `routing.yaml`'s `tiers.fast` list | Unit | Order list equality |
| F-GW-03 | An unknown tier falls back to `tiers.default`; a missing/empty `routing.yaml` falls back to `DEFAULT_TIER_ORDER` | Unit | Order list equality in both cases |
| F-GW-04 | A provider that raises once then succeeds on retry returns the success value and records exactly one ledger entry | Component (mocked adapter) | `ledger.entries` length == 1, is from the succeeding provider |
| F-GW-05 | A provider that fails both attempts moves on to the next candidate in order | Component | The second provider's adapter mock is called |
| F-GW-06 | Total failure across every provider raises `RuntimeError` whose message includes every provider's failure/skip reason | Component | All provider names appear as substrings of the exception message |
| F-GW-07 | An identical `(system_prompt, prompt, temperature)` call twice returns the cached value on the second call and adds **no** second ledger entry | Component | `ledger.entries` length == 1 after two `.call()`s |
| F-GW-08 | A cache hit bypasses rate-limit checks entirely — even a provider with an exhausted `RateState` still serves a cache hit | Component | Second call succeeds and returns cached text even with all `RateState`s pre-exhausted |
| F-GW-09 | `RateState.available()` returns `False` once `rpm_limit` calls have been recorded within the last 60 (simulated) seconds | Unit | Boolean assertion using explicit `now` values, no real sleeping |
| F-GW-10 | `RateState.record_failure()` makes `available()` return `False` until `cooldown_sec` has (simulated) elapsed | Unit | Boolean assertion at `now`, `now + cooldown_sec - 1`, and `now + cooldown_sec + 1` |
| F-GW-11 | `CostLedger.record()` computes cost as `(tokens/1000) * PRICE_PER_1K_TOKENS[provider]`, falling back to the default price for an unlisted provider | Unit | Numeric equality |
| F-GW-12 | `estimate_tokens("")` returns exactly `0` | Unit | Numeric equality (guards the explicit empty-string branch) |

### 4.3 DAG engine (`core/dag_engine.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-DAG-01 | A linear two-node chain (`b` depends on `a`) runs `a` to completion before `b` becomes ready | Unit (fake `tool_executor`) | `b`'s result reflects having run after `a`'s `SUCCEEDED` state was set |
| F-DAG-02 | Four independent nodes (no dependencies among them) all reach `SUCCEEDED` from a single `run()` call | Unit | All four in `store.results()` |
| F-DAG-03 | A failed node's direct child is marked `CANCELLED`, not executed | Unit (raising fake executor) | Child's `tool_executor` mock never called; child state == `CANCELLED` |
| F-DAG-04 | A failed node's **grandchild** (two levels down) is also `CANCELLED` (transitive propagation) | Unit | Grandchild state == `CANCELLED` |
| F-DAG-05 | A `Planner.on_outcome` returning a `GraphPatch(add=[...])` results in the new node appearing in `store.all_specs()` and eventually running | Unit (fake planner) | New node id present and `SUCCEEDED` |
| F-DAG-06 | A planner is invoked exactly once per finished node, not once per tick | Unit (counting fake planner) | Call count == number of nodes that finished |
| F-DAG-07 | `apply_patch` raises `ValueError` when a new node's `dependencies` references an id not yet in the graph | Unit | Exception raised before any node runs |
| F-DAG-08 | `apply_patch` raises `ValueError` if the resulting graph would contain a cycle | Unit | Exception raised |
| F-DAG-09 | `GraphStore.ready()` returns a node with zero dependencies immediately (vacuous truth over an empty predecessor set) | Unit | Node present in `ready()`'s return before anything else has run |
| F-DAG-10 | `record_outcome` with `error=None` and a *falsy* result (`[]`, `{}`, `0`) still marks the node `SUCCEEDED`, not `FAILED` | Unit | State assertion — this specifically guards the `error is not None` branch selector, not a truthiness check |
| F-DAG-11 | Checkpoint JSON written after a run contains one entry per node, matching `store.all_specs()` | Component (real filesystem, `tmp_path`) | Parsed JSON `nodes` list matches |
| F-DAG-12 | A non-JSON-serializable `result` (e.g. a raw `set()`) does not crash the checkpoint write; it's stored as its `str()` form | Component | Checkpoint file written successfully; the field is a string |

### 4.4 Harness record (`core/harness.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-HN-01 | `TaskRun.save()` then `TaskRun.load()` round-trips every field, including nested `Step`s | Component (`tmp_path`) | Loaded object equals original field-by-field |
| F-HN-02 | `save()` creates the target directory if it doesn't exist | Component | File exists at the expected nested path |
| F-HN-03 | `add_step()` appends in call order (steps list order == call order) | Unit | List order assertion |
| F-HN-04 | `TaskRun.load()` on a file missing the `steps` key defaults to an empty list, not an exception | Component | `loaded.steps == []` |

### 4.5 Ground-truth eval engine (`core/eval_framework.py`) and judge (`core/judge.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-EV-01 | All-passing axes produce `score == 1.0`, `passed == True`, `feedback_notes == "All rubric axes passed."` | Unit | Direct equality |
| F-EV-02 | A mix of passing/failing axes produces `feedback_notes` that **names the specific failed axis**, not a generic message | Unit | Substring assertion — this is a regression test for the bug fixed this repo already hit once |
| F-EV-03 | An axis function that raises is scored `False` and does not abort scoring of the remaining axes | Unit | The other axis's `True` result is still present in `EvaluationResult.axes` |
| F-EV-04 | `passing_threshold` comparison is inclusive (`>=`), verified at the exact boundary (4/5 axes passing with `threshold=0.80`) | Unit | `passed == True` at exactly `0.8` |
| F-EV-05 | `ground_truth_context` is passed through to every axis function unchanged | Unit (spy axis) | Spy records the exact object passed to `evaluate_run` |
| F-JD-01 | `judge_analysis` correctly parses a clean JSON response | Unit (fake gateway) | Field values match the fixture |
| F-JD-02 | `judge_analysis` correctly parses JSON wrapped in surrounding prose | Unit (fake gateway) | Field values match despite the wrapping text |
| F-JD-03 | Missing rubric fields in the response default to `0`/`""`, not an exception | Unit (fake gateway) | Defaults observed |
| F-JD-04 | `JudgeScore.average` computes `(specific + consistent + non_generic) / 15.0`, rounded to 2 decimals | Unit | Numeric equality at a few representative values (0, mixed, max) |

### 4.6 Agent (`capstone_agent.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-AG-01 | `DesignReviewPlanner.on_outcome` returns a `GraphPatch` when `get_readiness` succeeds with a non-empty `blockers` list | Unit | Patch not `None`; new node's `tool_name == "DesignChecklistResult.list"` |
| F-AG-02 | `DesignReviewPlanner.on_outcome` returns `None` when `blockers` and `reason_codes` are both empty | Unit | `None` returned |
| F-AG-03 | `DesignReviewPlanner.on_outcome` fires at most once per planner instance, even if called again with the same node | Unit | Second call returns `None` |
| F-AG-04 | `DesignReviewPlanner.on_outcome` ignores every node id other than `"get_readiness"` | Unit | `None` returned for e.g. `"get_versions"` |
| F-AG-05 | The follow-up checklist node's `tool_args` uses the real field name `overall_result`, not `status` | Unit | Dict key assertion — this is a direct regression test for the real bug this repo hit and fixed |
| F-AG-06 | `_mcp_tool_executor` raises `RuntimeError` when the MCP response contains an `"error"` key, even though the HTTP status was 200 | Component (fake `mcp_client`) | Exception raised; the raw error dict appears in the message |
| F-AG-07 | `_mcp_tool_executor` returns `structuredContent` when present | Component | Return value equals the fixture's `structuredContent` |
| F-AG-08 | `_mcp_tool_executor` falls back to the raw `result` dict when `structuredContent` is absent | Component | Return value equals the fixture's `result` |
| F-AG-09 | `run()` builds exactly 4 initial nodes with the documented ids (`get_file`, `get_versions`, `get_readiness`, `get_feedback`) and no inter-dependencies among them | Component (fake executor capturing the graph) | Node id set and dependency lists match |
| F-AG-10 | `run()` produces a `TaskRun` whose `steps` include one entry per initial node plus the planner's follow-up node when triggered | Component (fixture-driven fake tool executor returning a `release_readiness` fixture with blockers) | 5 steps present, in the right kinds |
| F-AG-11 | On synthesis-call failure, `run()` sets `run.error`, `run.ended == "error"`, and records a failed `llm_call` step, and **still calls `.save()`** | Component (fake gateway raising) | All three fields set; a `proofs/runs/*.json` file exists after the call |
| F-AG-12 | The synthesis prompt text includes the literal instruction not to claim `diff_from_parent_json` is populated | Unit (string assertion on the constructed prompt, extracted via a fake gateway that captures its input) | Substring present |

### 4.7 Ground-truth axes (`capstone_evals.py`)

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-AX-01 | `axis_cites_real_bend_radius_change` passes when both ground truth and the agent's claim contain the 2.0mm→3.0mm change | Unit (fake client) | `True` |
| F-AX-02 | `axis_cites_real_bend_radius_change` fails when ground truth supports the claim but the agent's answer omits either number | Unit | `False` |
| F-AX-03 | `axis_cites_real_bend_radius_change` fails when the agent's answer states the numbers but ground truth does **not** support them (agent hallucinated) | Unit | `False` — this is the axis's core "don't trust the agent's prose" property under direct test |
| F-AX-04 | `axis_matches_real_release_readiness` correctly scores `True` when `ready: false` and the agent says "not ready for release" | Unit | `True` — regression test for the negation-substring bug found and fixed this repo |
| F-AX-05 | `axis_matches_real_release_readiness` correctly scores `True` when `ready: true` and the agent says "ready for release" (the currently-untested, never-observed-live "happy governance" path) | Unit (synthetic fixture) | `True` |
| F-AX-06 | `axis_matches_real_release_readiness` scores `False` when the agent's direction contradicts the platform's real `ready` value in either direction | Unit | `False` in both directions, tested separately |
| F-AX-07 | `axis_did_not_fabricate_geometry_diff` fails on the exact fabrication phrases, passes on silence about geometry diffing | Unit | Both branches |
| F-AX-08 | `axis_judged_reasoning_quality` returns the judge's verdict against the `0.6` threshold correctly at, above, and below the boundary | Unit (fake judge) | Three boundary values |

### 4.8 `rescore.py`

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-RS-01 | Without `--with-judge`, `judged_reasoning_quality` is absent from the axes actually scored | Unit | Axis key absent from the result |
| F-RS-02 | With `--with-judge`, `judged_reasoning_quality` is present | Unit | Axis key present |
| F-RS-03 | Running against an empty `proofs/runs/` directory prints a clear message and does **not** write `results.json` | Component (`tmp_path`) | File does not exist after the call |
| F-RS-04 | Rescoring a saved run reproduces the same score as the original scoring pass, given unchanged ground truth | Component (fixture-backed fake client, fixed `TaskRun`) | Score equality |
| F-RS-05 | `results.json` is fully overwritten (not appended) across two separate `main()` invocations with different saved runs | Component | Second file's contents match only the second invocation's runs |

### 4.9 Entity / workflow-state-machine conformance (derived from `ENTITY_SCHEMAS.md`)

These tests exist because the assignment brief is explicit that "most business objects move through a state machine and the transitions carry the rules" (`agentswitch.md` §8, Step 1) — an agent that bypasses a transition action or attempts a role it doesn't hold is a correctness bug even if the final answer text looks fine.

| ID | Test | Type | Oracle |
|---|---|---|---|
| F-SM-01 | The agent never sends a raw `PATCH`/`update` call that sets a workflow entity's `status` field directly; every state change (if any is ever added to this agent's action set) goes through a named transition tool (`DesignReview.start_review`, `DesignFeedback.accept`, etc.) | Static/contract | Grep or AST-scan of `capstone_agent.py`'s `tool_args` construction for a literal `"status":` key on `.update` calls to workflow entities |
| F-SM-02 | The agent never attempts a `design_admin`-only transition (`DesignFile.approve_release` / `Approve Release`, `DesignProject`'s `design_admin`-gated transitions) — this seat is `design_user` | Static/contract + component | No such tool name appears anywhere in `capstone_agent.py`'s tool call sites; a component test asserting a hypothetical attempt would be rejected is a stretch goal once/if the agent ever needs write actions |
| F-SM-03 | If a future version of this agent creates or updates a `DesignReview`, `due_date` cannot be set to a date in the past | Unit (once write support exists) | Validates against the exact bug already filed by this team (`1b711d71-a408-4dc4-aff0-a26c30cc25cb`) — a live platform bug, not (yet) an agent-side check; this test documents that the agent-side code should defend against it even though the platform doesn't, since silently sending a past-dated request would currently succeed |
| F-SM-04 | Reading `DesignChecklistResult.overall_result` correctly handles all three enum values (`pass`, `fail`, `needs_review`) — the planner's current filter (`overall_result: "fail"`) silently excludes `needs_review` rows from the follow-up fetch | Unit — see edge case E-DS-06 for the concrete scenario | Documents current behavior; decide if `needs_review` should also trigger the follow-up, and test whichever answer is chosen |

### 4.10 End-to-end harness-level tests

These are the tests that most directly satisfy "the agent, answering your seat's questions against live data" and "at least one task where the correct answer is refusal." Several of these **must** run against the live platform at least once to count as real verification (per the brief's own worked example: Rillet-style grading watches the agent work against *live* data), but should also have an offline/fixture-backed counterpart in CI so the fast tiers still catch regressions.

| ID | Test | Type | Oracle |
|---|---|---|---|
| E2E-01 (**golden path**) | Running the agent against the real Bharat EV Battery Tray file produces a `TaskRun` with `claimed_answer` set, `ended == "done"`, and a `proofs/runs/*.json` file on disk | Live (nightly/manual) + Component (fixture-backed) counterpart | File exists; `EvaluationResult.score >= 0.8` via `capstone_evals.py` |
| E2E-02 (**refusal — insufficient evidence**) | Ask the agent the Section 8 question about a *noise* seed file with zero real version history or commit content (one of the ~91 files without a genuine multi-revision story, per `SESSION_NOTES.md` §1.1) | Live (uses real noisy seed data on purpose) | The agent's answer explicitly states it cannot support a revision-delta or manufacturability claim for this file, rather than inventing one. **This is the required refusal task from the brief** — write the hand-authored pass/fail check to fail loudly if the answer contains any of the specific fabricated-claim phrases from `axis_did_not_fabricate_geometry_diff`, or any invented numeric dimension not present in that file's real (sparse/absent) evidence. |
| E2E-03 (**refusal — out of scope**) | Ask the agent something the `design_user` role/seat cannot do (e.g. "approve this design for release" — `DesignFile`'s `ready → approved` transition is `design_admin`-only per `ENTITY_SCHEMAS.md` §2.1, and §5 rule 2 states the seat's `design_user` role cannot execute it) | Live or Component (mock the permission boundary) | The agent declines and names the permission boundary, rather than attempting the transition or claiming success |
| E2E-04 | A second full run of the *same* task (golden path) within a short window produces a `TaskRun` whose `claimed_answer` may differ in wording but whose **ground-truth axis verdicts are stable** (assuming the platform's underlying state hasn't changed in between) | Live | Axis-by-axis comparison across two real runs |
| E2E-05 | `rescore.py` run immediately after E2E-01, with no agent re-run, reproduces the same score | Live (cheap — no LLM synthesis call) | Score equality, confirming the raw-run/rescore split actually works end-to-end, not just in unit tests |

---

## 5. Edge case test suite

Organized by category. Each category starts with the platform/domain reality that motivates it (grounded in `PLAN.md`, `SESSION_NOTES.md`, and `ENTITY_SCHEMAS.md`, not hypothetical).

### 5.1 Data-shape edge cases (driven by real, documented platform quirks)

> Reality: `diff_from_parent_json` is `null` on every `DesignVersion` in this build (`PLAN.md` §8) and `has_brep` is `false` on all but one file, which itself has `conversion_status: "pending"` (never `completed`). Only ~9 of 100 `DesignFile` rows have any version history at all; only 5 of ~100 reviews are coherent (`SESSION_NOTES.md` §1.1).

| ID | Scenario | Expected behavior |
|---|---|---|
| E-DS-01 | `DesignFile` with exactly **one** `DesignVersion` (no "rev B" or "rev C" to compare at all) | Agent explicitly states there is no second revision to diff against — not a fabricated "no changes found," and not a crash on an assumed 2+-version list |
| E-DS-02 | `DesignFile` with **zero** `DesignVersion` rows | Agent explicitly states no version history exists; `DesignVersion.list` returning `{"data": [], "total": 0}` must not crash prompt construction (empty-list JSON dump is valid) |
| E-DS-03 | `DesignVersion.commit_message` is `null`/empty string on some or all versions in the chain | Agent does not fabricate content for the missing message; explicitly notes the gap |
| E-DS-04 | `release_readiness` returns `ready: true` with an empty `blockers` list (never observed live so far — every real file found is `ready: false`) | The planner does **not** add the follow-up checklist node (no blockers to chase); the governance axis correctly expects/accepts a "ready for release" claim |
| E-DS-05 | `release_readiness`'s `reason_codes` list contains a code not seen before (the platform adds a new one) | Agent still surfaces it as a blocking reason rather than silently ignoring an unrecognized code (this means the synthesis prompt should pass through `reason_codes` verbatim, which it currently does — verify it stays that way) |
| E-DS-06 | `DesignChecklistResult.overall_result == "needs_review"` (not `"fail"`, not `"pass"`) | Per F-SM-04 above: document and test the *chosen* behavior — currently silently excluded from the planner's follow-up query, which may or may not be correct |
| E-DS-07 | `DesignFeedback.list` for the target file returns 0 rows (true for the vast majority of the 100 seeded files) | Prompt section 5 ("Historical Feedback / Precedents") renders as an empty list, not an error, and the agent doesn't claim precedent that doesn't exist |
| E-DS-08 | The one file with `has_brep: true` eventually gets `conversion_status: "completed"` (platform fix) and a real `geometry_compared: true` diff becomes possible | Nothing in this codebase currently handles this path specially — worth a forward-looking test asserting the agent's prompt correctly incorporates a populated `diff_result_json` if one is ever real, rather than only ever working from `commit_message` text |
| E-DS-09 | `DesignVersion.metadata_json` / `assembly_tree_json` are populated (currently always `null` in observed data) with real structured content | Prompt construction includes this data if present, rather than the current implicit assumption that these fields are always empty |

### 5.2 Refusal / insufficient-authority edge cases

> Reality: the seat's `allowed_apps` are `["designreview", "agent", "crm"]`; every other app's data is a 403, "not a bug" per the brief. The seat's role is `design_user`, which cannot execute `design_admin`-only transitions (`ENTITY_SCHEMAS.md` §5, rule 2 — e.g. `DesignFile`'s `Approve Release`, §2.1).

| ID | Scenario | Expected behavior |
|---|---|---|
| E-RF-01 | Ask the agent to compare revisions of a `DesignFile` id that doesn't exist at all | Agent reports the file cannot be found; does not fabricate a plausible-sounding revision history |
| E-RF-02 | Ask the agent to compare revisions of a `DesignFile` belonging to a different company/tenant than the one it's authenticated against | A 403 (or the tool simply not existing in scope) is surfaced as "not permitted," not silently retried or misreported as "no data found" |
| E-RF-03 | Ask the agent to approve a design for release (`design_admin`-only) | Agent declines, states the permission boundary explicitly, and does **not** attempt the transition call at all (should never even reach the network) |
| E-RF-04 | Ask the agent a question entirely outside the `designreview`/`agent`/`crm` app scope (e.g. "what's our payroll liability this period") | Agent states this is outside its seat's scope, rather than attempting a tool call that doesn't exist for it |
| E-RF-05 | Ask the agent to run a geometry-based wall-thickness check on a file with `has_brep: false` | Agent states the platform's real, documented limitation (no CAD kernel in this build — the actual `501` response text) rather than inventing a wall-thickness number |

### 5.3 Concurrency / staleness edge cases

> Reality: the brief is explicit — "live data that other teams are changing underneath it" — and `PLAN.md` §3 notes the agent "must re-read before acting, never assume staleness is safe."

| ID | Scenario | Expected behavior |
|---|---|---|
| E-CC-01 | The target file's `release_readiness` changes (a blocker gets resolved) between the `get_readiness` node completing and the final synthesis prompt being built | Out of this agent's current control (single-pass evidence gathering, no final re-verification step) — **this is a real, currently-unaddressed gap**: document it as a known limitation and add a test that at minimum proves the agent uses the evidence gathered *at the time each node ran*, consistently, rather than mixing an old cached value with a newer one within a single run |
| E-CC-02 | Two nodes in the same tick (e.g. `get_file` and `get_versions`) complete out of order across repeated runs (thread scheduling is not guaranteed deterministic) | The final synthesis prompt's evidence sections are populated correctly regardless of completion order — this is really a restatement of F-DAG-02/F-AG-09 under an explicit "order shouldn't matter" framing |
| E-CC-03 | The MCP token expires mid-run (a long-running multi-node graph outlives the session token's TTL) | Currently: `as_client.py` has no re-login-on-401 logic (see `as_client.md`'s tests-to-add #2) — a run in this state fails with a `RuntimeError`, which `core/dag_engine.py` correctly records as a `FAILED` node rather than crashing the whole process; test that this degrades to a partial, honestly-incomplete `TaskRun` rather than a silent hang or a fabricated answer built from only some of the intended evidence |

### 5.4 Malformed-input / encoding edge cases

| ID | Scenario | Expected behavior |
|---|---|---|
| E-EN-01 | A `commit_message` containing non-ASCII characters (the real data already has an en-dash, `—`, which this project's own Bash tooling mangled into `�` at one point during manual investigation — a genuine, previously-observed encoding hazard) | The full pipeline (MCP JSON → Python str → prompt text → LLM → `TaskRun.save()` JSON) preserves the character correctly; verify with a round-trip test through `TaskRun.save()`/`.load()` specifically, since that's a JSON (de)serialization boundary |
| E-EN-02 | A `commit_message` or `DesignFeedback.tags` field is an unexpected type (e.g. `tags` as a list instead of a string, or `None`) | Defensive test motivated directly by the real, already-filed UI bug (`fb28c793-...`, `TypeError: e.tags.split is not a function`) — if this agent's own evidence-formatting code ever calls `.split()` or similar string methods on a platform field, it must guard against the same malformed shape that broke the UI, rather than assuming the field is always a clean string |
| E-EN-03 | An MCP tool response body is truncated or contains invalid JSON (a genuine transport-level failure, not a JSON-RPC error envelope) | `as_client.py`'s `json.loads(body)` raises `json.JSONDecodeError`; test that this propagates as a clear failure rather than being silently swallowed anywhere in the call chain |
| E-EN-04 | An extremely long `commit_message` chain (many versions, each with a long message) pushes the synthesis prompt close to or past the provider's context/token limit | Test that the gateway's `max_tokens` parameter governs the *response* size correctly, and separately identify (this is more a finding than a fixable unit test) whether there's any *input*-side truncation — currently there is none, which is a real scalability edge case worth flagging even if not fully solved |

### 5.5 DAG/graph structural edge cases

| ID | Scenario | Expected behavior |
|---|---|---|
| E-DAG-01 | A planner returns a `GraphPatch` referencing a `dependencies` id that was itself just cancelled in the same patch | `apply_patch`'s two-pass design (nodes first, then edges, then cancellations) means this is well-defined — write a test pinning down the exact resulting state rather than leaving it as an accident of implementation order |
| E-DAG-02 | A planner keeps returning new follow-up nodes forever (a runaway/buggy planner) | There is currently **no cap** on total nodes or total ticks in `LiveGraphExecutor.run()` — this is a real, unaddressed risk (an infinite-patch planner would loop forever, or at least until the process is killed). Add a test proving the current `DesignReviewPlanner`'s one-shot guard (`_added_followup`) prevents this *for that specific planner*, and separately flag (or implement) a generic max-node/max-tick safety limit in `LiveGraphExecutor` itself as a defense-in-depth measure, since a future planner might not have an equivalent guard |
| E-DAG-03 | `GraphStore.ready()` is called on an empty graph (before any `apply_patch`) | Returns `[]`, not an exception |
| E-DAG-04 | Two `TaskSpec`s are added with the same `id` in two different patches | `networkx.DiGraph.add_node` silently overwrites the existing node's data on a duplicate id — test and document this (it means a second `TaskSpec` with a reused id silently replaces the first's `spec` data entirely, potentially losing recorded state) |

---

## 6. Performance test suite

Baseline numbers below come from real observed runs during development (see `SESSION_NOTES.md` and this project's own `proofs/runs/*.json`), not invented targets — use them as a starting budget and tighten once more data exists.

| ID | Test | Metric & method | Budget (observed baseline) |
|---|---|---|---|
| P-01 | End-to-end single-run wall-clock time | Time `Team21DesignReviewAgent.run()` start to return | Observed: ~30-50s dominated by the single Gemini synthesis call (~14-30s) plus MCP round trips (sub-second each). Flag a regression if a run exceeds **90s** with no synthesis retry involved. |
| P-02 | MCP round-trip latency per tool call | Time a single `_mcp_tool_executor` call in isolation | Observed: comfortably sub-second for `.get`/`.list`/`endpoint.*` calls against Suryodaya. Flag if any single call exceeds **5s** (excluding the platform's own occasional cold-start latency). |
| P-03 | **Concurrency is real, not accidental serial execution** | Run 4 independent nodes whose fake `tool_executor` each `time.sleep(0.2)`; assert total tick wall-clock is closer to 0.2s than 0.8s | Should be within ~50% of the single-node duration, not ~4× it — this is the test that would catch a regression to accidental sequential execution even though final results would still be "correct" |
| P-04 | Gateway failover overhead when a provider is skipped for missing key | Time `.call()` when only the last-tier provider has a key configured | Skipping providers via `_key_present` should add negligible (sub-millisecond) overhead per skip — no network call should be attempted for a provider with no key |
| P-05 | Gateway failover overhead when a provider is skipped for rate-limiting | Same, but via a pre-exhausted `RateState` | Same — zero network calls for a rate-limited skip |
| P-06 | Cache hit latency | Time a second identical `.call()` after the first cached it | Should be near-instantaneous (dict lookup + hash), several orders of magnitude faster than any real provider call — assert e.g. `<10ms` |
| P-07 | Cost ledger accuracy at volume | Record 100 synthetic calls via `CostLedger.record()`, assert `total_cost()`/`total_tokens()` match a manually-computed sum | Exact equality — a property-based test (`hypothesis`) is a good fit here |
| P-08 | Checkpoint write cost scaling | Measure `GraphStore._checkpoint()` wall-clock time as node count grows (e.g. 5, 50, 500 nodes) | Each checkpoint currently rewrites the *entire* graph state to disk — confirm this scales roughly linearly with node count (not worse), and use this measurement to decide whether this project's node counts (~5-10) ever justify optimizing it (they currently don't) |
| P-09 | Large-evidence-payload handling | Construct a `DesignVersion.list`/`DesignFeedback.list` response fixture at the real API's max `limit` (1000 rows) and measure prompt-construction time and resulting prompt character count | Identify at what row count the resulting prompt would risk exceeding a typical provider's context window (informational — likely reveals the need for evidence summarization/truncation before it's ever hit in practice at this project's actual data scale) |
| P-10 | Judge-axis added latency | Time `axis_judged_reasoning_quality` in isolation (one extra LLM call on the `"fast"` tier) | Should be materially faster than the main synthesis call (`"fast"` tier is chosen specifically for this); flag if it ever approaches or exceeds the synthesis call's own latency |
| P-11 | Rescore throughput | Time `rescore.py` scoring N saved runs (without `--with-judge`) | Should scale with the number of *live ground-truth re-fetches* (network-bound), not with any local computation — since axes re-fetch on every call (see `capstone_evals.md`'s efficiency note), this should also motivate P-11a below |
| P-11a | Redundant ground-truth fetch count | Instrument a fake client's `call_mcp` with a call counter; run the full `AXES` dict through `evaluate_run` once; assert how many times `DesignVersion.list` was called | Currently: twice (once per axis that needs it) for a single scoring pass — this is a concrete, measured "before" number for a future optimization, not a pass/fail test on its own |

---

## 7. Non-functional / cross-cutting tests

| ID | Test | Why it matters |
|---|---|---|
| N-01 | No secrets (API keys, bearer tokens, passwords) ever appear in a `proofs/runs/*.json` file or a `proofs/checkpoints/*.json` file | `TaskRun`/`GraphStore` serialize tool call results and errors verbatim — an error message from `as_client.py` that happens to echo back a request header would leak a credential into a committed-adjacent proofs directory. Grep every saved fixture/test-generated file for the literal `.env` values used in the test environment. |
| N-02 | No secrets appear in log output (`logger.info`/`logger.error` calls throughout `core/`) | Same concern, different sink — CI log capture is a common accidental leak vector. |
| N-03 | TLS verification is disabled (`verify=False`) only where currently intentional (`as_client.py`'s `ssl._create_unverified_context()`, `core/llm_gateway.py`'s Gemini adapter) and this is a **known, flagged risk**, not silently expanding | A regression test asserting *exactly* which HTTP clients have verification disabled, so a future refactor doesn't accidentally disable it somewhere new — and a standing action item to investigate whether the Gemini-only asymmetry (noted in `core_llm_gateway.md`) is still necessary. |
| N-04 | Idempotency of ground-truth axes: calling the same axis twice in immediate succession against unchanged platform state returns the same verdict both times | Guards against any accidental non-determinism creeping into an axis (e.g. depending on wall-clock time, random ordering of a `.list` response with no explicit `sort_by`). |
| N-05 | `requirements.txt`-pinned dependency versions install cleanly in a fresh virtual environment | Basic reproducibility check — run in CI on a schedule (not every push) since it's about environment drift over time, not code correctness. |

---

## 8. Environment & CI recommendations

- **Default CI command**: `pytest -m "not requires_live_api"` — runs Tiers 1-3 (contract checks, unit, component/fixture-backed) on every push. Should complete in well under a minute given nothing here is genuinely slow once mocked.
- **Live suite**: `pytest -m requires_live_api` — run manually, or on a schedule (e.g. nightly), never on every push, because it costs real LLM-provider money and makes real (if read-only) calls against a shared platform other teams are also using.
- **Fixture refresh discipline**: whenever the live platform's schema or a specific record's content changes in a way that breaks a fixture-backed test, that's a signal to re-capture the fixture — treat a fixture going stale as information (something changed), not just an annoyance to silence.
- **Register the marker** in `pyproject.toml` (a `[tool.pytest.ini_options]` `markers = ["requires_live_api: needs real AgentSwitch/provider credentials"]` block) so `pytest --strict-markers` doesn't reject it.

---

## 9. Why "does the output text contain the right keywords" is not an acceptable substitute for any of the above

Worth stating explicitly, since it's the exact failure mode this whole project's `core/eval_framework.py` was built to avoid (see `capstone_evals.md`'s trade-offs section for the full history): a test that asserts `"bend radius" in result.claimed_answer` is not a test that the agent is *correct* — it's a test that the agent said a certain phrase. Every functional test in §4.6/§4.7 above that exercises ground-truth-checking logic (F-AX-01 through F-AX-08 especially) exists specifically to verify the axis re-fetches and compares against **real platform state**, not the agent's own words. When implementing any of these by hand, resist the temptation to fake the ground-truth fetch with a value copy-pasted from the expected answer — fake it with an *independently constructed* fixture value, and write at least one test per axis (F-AX-03 is the template) where the agent's claim and the ground truth deliberately **disagree**, to prove the axis actually catches that case.

---

## 10. Prioritized implementation order

If implementing incrementally rather than all at once, this order maximizes coverage-per-hour and front-loads the tests most directly tied to the grading rubric:

1. **§4.10 E2E-02 (the refusal task)** — required by the brief; zero partial credit for skipping it.
2. **§4.7 F-AX-03 through F-AX-06** — these are the tests proving the harness's core "verifiers that read the database" property, the second-most-quoted grading criterion.
3. **§4.3 F-DAG-01 through F-DAG-10** — cheapest-to-write, highest-density coverage of the structurally most important file, zero mocking required.
4. **§4.6 F-AG-05** — a one-line regression test for a real bug already found; trivial to write, protects against it silently coming back.
5. **§4.9 F-SM-01/F-SM-02** — directly enforces the assignment's explicit "invoke transitions, don't PATCH status" and "don't exceed your role" rules.
6. Everything else in §4-§7, roughly in the order presented, as time allows.
