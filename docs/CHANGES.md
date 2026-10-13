# Changes

## 2026-10-13 — Docs: architecture document; explainers caught up; hand-written retrieval tests

| What | Before | After |
|---|---|---|
| `docs/ARCHITECTURE.md` (new) | genericity was spread across the explainers | one document: the two layers, the pack seam (`Registry` slots, required or not), one run end to end, the extension points and their interfaces, how genericity is enforced, honest limits; linked from `README.md` and `docs/EXPLAINERS.md` |
| `agentkit_record.md`, `agentkit_transport.md`, `capstone_evals.md`, `EXPLAINERS.md` | did not mention the `paused` run state, the journal's `start_seq`, replay calibration or the new engine features | they do |
| `tests/test_retrieval.py` | to-do template | three hand-written chunker tests (verbatim-suffix split, paraphrase rejected, splitter failure) |
| `.gitignore` | `PROVENANCE.local.md` only | any `*.local.md` stays local |

## 2026-10-13 — Engine: retry policy, channels + superstep mode, checkpoint history + fork/replay, approval gate; parent-child retrieval; narrator hardening (integration Phase 6)

### Why

The graph engine could only run eagerly, retry with one hard-coded rule, and remember its latest state. The
harness could prove each *check* fails on a broken record, but not that a wrong *platform answer* travels all
the way to a failed grade. Live write runs had no stop for a person. And three paper findings (grounded
self-correction, prompt injection through platform text, reasoning tags in answers) were not acted on.
Algorithms for the engine and retrieval parts are adapted from external open-source designs (referred to from
outside sources) and rewritten in plain Python; nothing is imported and `requirements.txt` is unchanged.

### Added / changed (before → after)

| What | Before | After | Where |
|---|---|---|---|
| Retry policy | `retries=<int>`, doubling waits, FLAKY only | `RetryPolicy(max_attempts, initial_interval, backoff_factor, max_interval, jitter, retry_on)`; `retries=n` keeps the exact old waits; writes always one attempt; each retry a span event | `agentkit/graph/policies.py`, `registry.py`, `engine.py` |
| Scheduling | eager only | + opt-in `mode = "superstep"`: a step's nodes run on one snapshot; outcomes, channel writes and planner patches applied in node-id order; `max_steps` → `StepLimitExceeded`. Shipped plans stay eager | `engine.py`, `plan_loader.py` |
| Channels | none | `last_value`, `topic`, `reducer` (+`Overwrite`), `ephemeral`, `barrier`; declared in a plan's `[channels]`; `NodeResult.writes`; `read_channels()`; conflicting single-value writes in one step are an error | `agentkit/graph/channels.py` |
| Pause / approval | — | `interrupt(payload)` pauses a node; `store.resume()`; `approve_writes=True` pauses every real write with its preview; `run_one --write --require-approval`, then `run_one --resume <dir> --approve/--reject <node>`; new `ended = "paused"` | `engine.py`, `harness/run_one.py`, `record/run_record.py` |
| History / fork | `graph.json` = latest state | optional `MemorySaver` / `JsonlSaver` (time-ordered ids, `list(before, limit, filter)`); `GraphStore.fork()` replaces node results and undoes everything downstream; rules never add a node twice | `agentkit/graph/checkpoint.py`, `engine.py`, `rule_planner.py` |
| End-to-end calibration | mutants edit finished records only | `agentkit.harness.replay`: replace one node's result, re-run downstream offline, rebuild finding + answer, grade. `--calibrate`: every pack replay fault must fail its target check, every control replay must still pass. In CI | `agentkit/harness/replay.py`, `packs/designreview/replay_faults.py`, `ci.yaml` |
| Retrieval | hit = matching passage | `ParentChildIndex`: search children, return the whole parent (rank-order de-dup, missing parent dropped); `split_by_headers` (header paths, code fences respected). The pack's standards action uses it; matches gain `section` | `agentkit/retrieval/hierarchy.py`, `packs/designreview/actions.py` |
| Rewrite feedback | "remove these quantities" | names the closest verified values and their finding paths; `ClaimRules.explain` hook | `agentkit/answer/claim_audit.py`, `narrator.py` |
| Platform text in prompts | finding JSON pasted raw | fenced in `<untrusted_platform_data>`, tag copies escaped, `DATA_RULE` always in the system prompt; pack `system.md` rule | `narrator.py`, `packs/designreview/prompts/system.md` |
| Reasoning tags | kept in the answer | `strip_reasoning()` before ledger, cache and audit; `reasoning_chars` on the reply and span | `agentkit/llm/gateway.py` |
| Small | `run_one` alone wrote no `task.json`; journal numbering restarted at 1 | writes it when missing; `JournaledSeatClient(start_seq=)` so a replay / resume continues the numbering | `run_one.py`, `transport/journal.py` |
| Docs | — | graph, answer, retrieval, llm, harness, pack explainers; README; `tests/` templates (docstrings only) + new `tests/test_retrieval_hierarchy.py` template | `docs/`, `tests/` |

### Results

| Run | Result |
|---|---|
| Offline batch ×3, `grade --check`, mutants | 9 of 9 on every run; identical; CALIBRATION OK |
| Replay calibration | `gate_reports_ready` 7/7, `latest_note_altered` 2/2, `milestones_unreadable` 2/2 caught; controls 9/9 still pass |
| Parent-child retrieval vs before | findings identical on all 9 offline tasks |
| Superstep vs eager | findings identical on all 9 offline tasks |
| Approval gate on t08 (offline) | pauses with 2 proposed hand-offs and no write call; reject → `declined`, answer written; approve → each hand-off filed once |
| Fault matrix, viewer smoke | 7 of 7; ok |
| Bugs found by the new tests and fixed before commit | superstep checkpoint metadata overwrote the node-status map (reserved keys now refused); `section_children` copied parent metadata past `child_meta_fields` |

## 2026-10-12 — CI: fault-injection matrix and viewer smoke check; viewer truncation fix

### Why

CI proved the happy path (9 of 9, identical re-grading, calibration). It didn't prove that the system
degrades safely when the platform misbehaves, or that the viewer people use to explain runs actually works.

### Added / fixed (before → after)

| What | Before | After | Where |
|---|---|---|---|
| Fault matrix | faults only tried by hand | 7 scenarios replayed in CI: each must finish the agent run and be graded exactly as declared (overall grade + named checks). Table in the job summary | `scripts/ci_fault_matrix.py`, `packs/designreview/ci_faults.toml`, `.github/workflows/ci.yaml` |
| Viewer smoke | not checked | CI starts the viewer as its own process on the CI batch and opens the page, static files, batch, report, calibration and every run (records + telemetry tree); POST must give 405 and path traversal 404 | `scripts/ci_viewer_smoke.py`, `ci.yaml` |
| **Viewer bug** | each response body sent with one `wfile.write()`: on Windows loopback 1-4 % of large (~130 KB) run pages stopped at ~64 KB and the client hung | body written in 64 KB pieces: 300 of 300 large responses complete | `agentkit/viewer/server.py` |
| Docs | — | README CI list; sim and viewer explainers | `README.md`, `docs/explainers/` |

### Results

| Run | Result |
|---|---|
| Fault matrix | 7 of 7 as expected; a deliberately wrong expectation is reported as MISMATCH (exit 1) |
| Viewer smoke | ok 5 of 5 runs; a run with its telemetry deleted is reported (exit 1) |
| Viewer stress, one 130 KB run page | before: 96-97 of 100 complete; after: 300 of 300 |
| Isolation of the cause | same 130 KB body, single write vs 64 KB writes, 200 fetches each: 197 / 200 vs 200 / 200 |

## 2026-10-12 — Telemetry: a run tree per run (integration Phase 5)

### Why

A run's timings were spread over three files (node times in `graph.json`, `seconds` per journal line, LLM
latency in `llm_ledger.json`) with nothing linking a tool call to the node that made it or showing whether
the parallel reads really ran in parallel. The session notes asked for end-to-end tracing, latency profiling
and token accounting **without** an external SDK.

### Added (before → after)

| What | Before | After | Where |
|---|---|---|---|
| Span engine | none | `Tracer`, `span()`, `traced`, `use_tracer`, `bind_context`, `annotate`, `add_event`; `trace_id` / `dotted_order` / `run_type` run-tree fields; start / end / error callback handlers. No-op without a tracer | `agentkit/telemetry/spans.py` (new) |
| Tree, metrics, export | none | `build_tree`, nearest-rank `percentile`, `summarize`; `runs/<run_id>/telemetry.json` (`agentkit-telemetry-v1`) | `runtree.py`, `metrics.py`, `exporter.py` (new) |
| Instrumentation | — | task (run_one), node + planner (engine, context bound into the pool), tool (journal), wire status/bytes + re-login (seat_rpc), llm (gateway), build_finding + narrate (agents) | `agentkit/harness/run_one.py`, `graph/engine.py`, `transport/journal.py`, `transport/seat_rpc.py`, `llm/gateway.py`, `agents/planned.py` |
| Batch report | status table | + `## Performance` (run ms, nodes, tool calls, tool p50/p95, LLM calls, tokens, cost) — report only, never in `batch_score.json` | `agentkit/harness/grade.py` |
| Viewer | 7 tabs | + Timeline tab over `telemetry.json` | `agentkit/viewer/server.py`, `static/app.js` |
| Docs | — | new `agentkit_telemetry.md`; graph / LLM / viewer explainers; `EXPLAINERS.md` reading order; `tests/test_telemetry.py` to-do list (docstring only) | `docs/`, `tests/` |

Provenance: the run-tree design (dotted ordering, context-variable propagation, start / end / error
callbacks) is adapted from an external open-source tracing approach, referred to from outside sources and
rebuilt in plain Python with no third-party dependency.

### Results

| Run | Result |
|---|---|
| `tests_local/test_telemetry.py` | 18 passed (spans, threads, tree, metrics, export, handlers, dotted order, batch integration, LLM span) |
| `tests_local` offline (`AGENTKIT_OFFLINE=1`) | all green |
| Offline batch | 9/9; every run has one `task` root, node spans = executed nodes, tool spans = journal lines |
| Viewer Timeline (Chrome, t01) | renders, no console errors; shows `revisions`, `release_gate`, `feedback` starting within 0.5 ms of each other after `design_file` — the 3-way parallel superstep, measured |

## 2026-10-12 — Catalogue fingerprint, platform-safe reads, pre-flight, re-captured fixtures (integration Phase 4)

### Why

The fixtures recorded tool *names* only, so nothing could tell that the seat's catalogue had changed (it had:
302 → 323 tools since 2026-09-30). The pack read lists with a 50-row cap, a server-side sort the platform
doesn't validate, and filters it never re-checked. Running the new live pre-flight then exposed two transport
bugs that made a second tenant unusable on any machine whose `.env` holds the runner's `AGENTSWITCH_TOKEN`.

### Changed (before → after)

| What | Before | After | Where |
|---|---|---|---|
| Catalogue | `seat_catalog()` dict | + `ToolCatalog` (fingerprint, required args, readOnlyHint, requirement check); `catalog()` on `SeatRpcClient` and `SimPlatform` | `agentkit/transport/catalog.py` (new), `seat_rpc.py`, `sim/sim_platform.py` |
| Run record | — | `taskrun.tool_catalog = {fingerprint, count, problems}` | `agentkit/record/run_record.py`, `agentkit/harness/run_one.py` |
| List reads | 50 rows, server sort, filters trusted | `safe_reads.read_all`: exact safe filters only, paged to the end, sorted in code, every filter re-checked (`ne:` done right) | `agentkit/transport/safe_reads.py` (new), `packs/designreview/actions.py`, `handoffs.py` (`list_milestones`) |
| Pack requirements | implicit | `REQUIRED_TOOLS` → `extras["required_tools"]`; `SAFE_SERVER_FILTERS` | `packs/designreview/actions.py` |
| Pre-flight | none | `python -m agentkit.harness.preflight --tenant t`: required tools + fingerprint vs fixture (read-only) | `agentkit/harness/preflight.py` (new) |
| Fixtures | names only, captured 2026-09-30 | + `tool_defs`, `tools_fingerprint`; **re-captured live 2026-10-12** (both tenants, 323 tools, fingerprint `fbadee4421f15020`) | `agentkit/sim/fixture.py`, `packs/designreview/fixtures/*.json` |
| Token per tenant | `SeatSession` used `AGENTSWITCH_TOKEN` for every tenant → 401 on the other one | only for the tenant `tenant_login` resolved without a password | `agentkit/transport/seat_rpc.py` |
| Error bodies | `HTTPError.read()` crashed on the platform's mis-framed 401 (`IncompleteRead`) | `read_error_body` tolerates it, so the 401 reaches the re-login | `agentkit/transport/wire.py` |

Because the fixtures were re-captured on 2026-10-12, offline replays now answer as of that day: t08's past-due
list includes "PPAP level 3 submission" (due 2026-10-03) as well as "DFM review closed".

### Results

| Run | Result |
|---|---|
| Pre-flight, both tenants (live) | required tools ok; fingerprint `fbadee4421f15020` = fixtures |
| Offline batch on the new fixtures, `--repeat 3` | **9 of 9 tasks pass on every run**; `--check` identical; calibration OK |
| Live dry-run batch, template answers | **9 of 9** pass; calibration OK; every run records the fixture's fingerprint, no problems |
| `tests_local` offline (`AGENTKIT_OFFLINE=1`) | 598 passed, plus 4 new transport regression tests |
| `tests_local` live read-only | 29 passed, 3 xfailed — **only with `AGENTSWITCH_*` blanked**: with the Release 8.1 runner variables in `.env`, `tenant_login("suryodaya")` returns no password and `tests/conftest.py`'s password login gets 401 (26 errors). Pre-existing since the 8.1 runner change; not fixed here |

## 2026-10-12 — Offline means offline; test hygiene (integration Phase 3)

### Why

CI promises "100 % offline", but nothing enforced it: an offline run that reached for the platform or an LLM
would just try to connect (and on a developer machine, succeed). Separately, a developer's `.env` leaked into
offline reference tests: `AGENTSWITCH_TOKEN` (loaded process-wide when `as_client` is imported) made
`SeatSession` skip its login, so two fake-wire login tests failed on any machine with that variable set.

### Changed (before → after)

| What | Before | After | Where |
|---|---|---|---|
| Offline switch | none | `offline_mode()` reads `AGENTKIT_OFFLINE` (1 / true / yes) | `agentkit/config.py` |
| Platform wire | always connects | raises `OfflineViolation` (a `RuntimeError`, not a `WireUnreachable`, so it is never retried or turned into a refusal) when offline and `urlopen` is the real one | `agentkit/transport/wire.py` |
| LLM gateway | always connects | same guard when using the real network (an injected `transport=` is exempt) | `agentkit/llm/gateway.py` |
| Narrator | any LLM error → template fallback | `OfflineViolation` is re-raised, so the run ends `error` | `agentkit/answer/narrator.py` |
| Reference tests | inherited `.env` | autouse fixture removes `AGENTSWITCH_*` for tests not marked live / agent / gateway_live | `tests_local/conftest.py` (git-ignored) |
| Docs | — | transport + LLM explainers; `tests/test_seat_rpc.py` to-do list (docstring only) | `docs/explainers/`, `tests/` |

### Results

| Run | Result |
|---|---|
| `tests_local` offline | **579 passed**, 0 failed (was 2 failed: the `.env` leak) |
| `tests_local` offline with `AGENTKIT_OFFLINE=1` | 579 passed — no offline test touches the network |
| `tests/` with `AGENTKIT_OFFLINE=1` | 35 passed (+ the in-progress hand-written `test_retrieval.py`) |

Not done (deferred, low value): `tests_local/conftest.py` still re-uses fixtures from `tests/conftest.py`.

## 2026-10-12 — CI and secret scanning (integration Phase 2)

### Why

The project had no CI: every guarantee (9/9 offline, deterministic grading, calibrated verifiers, no
committed credentials) held only when someone remembered to run it. The workflow drafted in the session notes
would not have worked as written: it replayed one tenant's fixture (so the three Keystone tasks fail), used a
`--batch-id` flag and a report line that didn't exist yet (added in Phase 1), and "re-scored" by overwriting
rather than comparing.

### Added (before → after)

| What | Before | After | Where |
|---|---|---|---|
| CI | none | 4 legs (ubuntu-24.04 / windows-2025 × py3.11 / 3.13): `tests/` offline, offline batch on the fixtures **folder** ×3 with the `9 of 9 tasks pass on every run` gate, `grade --check`, calibration; job summary; run folders uploaded on failure only after they pass the secret scan. Read-only token, actions pinned by commit SHA (checked against their tags), `AGENTKIT_OFFLINE=1` | `.github/workflows/ci.yaml` |
| Secret scan (ours) | none | stdlib scanner: AgentSwitch JWT, Anthropic / OpenAI / OpenRouter / Gemini / Groq / NVIDIA / Cerebras keys, UPPER_CASE secret variables or quoted password/secret/token/api_key with a literal value (letters + digits), committed `.env`; tracked files, given paths, or `--history` (every added line, every ref); never prints a value | `scripts/secret_scan.py` |
| gitleaks | run by hand with default rules (README) | CI job, v8.30.1 tarball SHA-256-checked (checksum fetched and verified 2026-10-12); default rules + AgentSwitch JWT, NVIDIA, Cerebras, Groq, `AS_*_PASSWORD`; `.gitleaksignore` fails the job, inline allows ignored | `.gitleaks.toml`, `ci.yaml` |
| README | gitleaks command, "28 commits" | both scanners, CI section | `README.md` |

### Results

| Run | Result |
|---|---|
| CI steps replayed in a clean worktree (no `.env`, no `tests_local/`) | `tests/` 35 passed; 9 of 9 tasks pass on every run (×3); `--check` identical; calibration OK; secret scans clean |
| `secret_scan.py --history` / tracked files | clean (first draft flagged 3 false positives — `TOKEN_EXPIRED = "token_expired"`, `os.environ[...]`, a `Dict[...]` annotation — fixed by excluding brackets and requiring letters + digits in assignment values) |
| `gitleaks git . --config .gitleaks.toml` | no leaks (31 commits); a planted NVIDIA key and `AS_*_PASSWORD` are both caught |
| `tests_local/test_secret_scan_ci.py` | 16 passed |

Not verified here: the workflow itself on GitHub (needs a push; see the verification list in the plan).

## 2026-10-12 — Evals you can gate on: frozen clock, pass^k, checkable re-grading (integration Phase 1)

### Why

A CI gate has to give the same verdict on any machine and any day, and it has to *prove* that grading is
deterministic rather than assume it. Three things stood in the way:

- The finding's `snapshot_at` came from the wall clock, so t08/t09's past-due milestones changed as real dates
  passed (a reference test started failing on 2026-10-03, when "PPAP level 3 submission" fell due).
- `dfm_standards` built its search query from the change notes **plus the release blockers "if the gate had
  answered by then"**, so the cited standards depended on which thread finished first. Found by the new
  determinism test: two identical offline runs in one process cited different standards. Live runs had the
  same race.
- There was no way to run each task several times, no batch-level pass/fail line, and re-grading just
  overwrote the scores.

### Changed (before → after)

| What | Before | After | Where |
|---|---|---|---|
| Time source | `datetime.now()` in the finding and the ground-truth capture | `agentkit.clock.now_utc()`; `AGENTKIT_FROZEN_NOW` (ISO-8601 with timezone) overrides it; `frozen_now(iso)` pins it for a block | `agentkit/clock.py` (new), `packs/designreview/finding.py`, `agentkit/harness/ground_truth.py` |
| Offline replays | answered "as of today" | each task runs frozen at its tenant fixture's `captured_at` (suryodaya 2026-09-30T15:45:09Z, keystone 2026-09-30T15:46:03Z) | `agentkit/harness/batch.py` (`SimSession.run_agent`) |
| Standards query | `dfm_standards` waited for `revisions` only; blockers added if the gate happened to be done | waits for `revisions` **and** `release_gate`; the query always carries the blocker titles. If the gate read fails, the standards section is now bypassed rather than searched with a thinner query | `packs/designreview/plans/rev_diff_dfm.toml` |
| Batch id | always a timestamp | `--batch-id <id>` (pattern `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`, never reuses an existing batch; CLI error exit 2) | `agentkit/harness/batch.py` |
| Repeats | one run per task × tenant | `--repeat N`; run ids get `-r1..-rN` (unchanged for N=1); manifest records `repeat`, `expected_runs`, `expected` per task | `agentkit/harness/batch.py` |
| Batch verdict | per-run table only | pass^k: a task passes only if every run passed and none is missing. `report.md` states `**P of T tasks pass on every run.**`; `batch_score.json` (no timestamps) holds the numbers; a "Runs that never happened" section lists expected runs missing from the manifest | `agentkit/harness/grade.py` |
| Re-grading | overwrote `score.json` | `grade --check <batch>` / `rescore_check()`: re-grade in memory, write nothing, compare with saved `score.json` + `batch_score.json`; exit 1 on any difference or an ungraded batch | `agentkit/harness/grade.py` |
| Tool journal | created on the first tool call (t03 had none) | created empty before any work | `agentkit/harness/run_one.py` |
| Docs | — | harness, sim, evals and pack explainers; README CI recipe; `tests/test_harness.py` to-do list (docstring only) | `docs/explainers/`, `README.md`, `tests/` |

Unchanged on purpose: run ids when `--repeat` is 1, `score.json` format, `report.md` table, check statuses,
and every existing CLI flag.

### Results

| Run | Result |
|---|---|
| Offline CI recipe: `batch --sim packs/designreview/fixtures --skip-llm --batch-id ci --repeat 3 --grade` | **9 of 9 tasks pass on every run** (27 runs) |
| `grade --check runs/batches/ci` | identical |
| Calibration on that batch | OK: no mutant missed, no check unexercised |
| `tests_local` offline | 553 passed, 2 failed (both `.env` `AGENTSWITCH_TOKEN` leaking into `SeatSession` tests; fixed in Phase 3), 31 skipped (later phases) |
| `tests/` | 35 passed, 1 failed (`test_retrieval.py`, hand-written, in progress) |

## 2026-09-30 — Write mode: hand-offs filed as escalations, verified, withdrawn (Part 3)

### Why

Part 2 proposed hand-offs but never delivered them. Part 3 files them on the platform so a person is actually
on the hook, proves from ground truth that exactly the right escalations exist, and then cleans up. The
platform is shared, so a test run must not leave tickets in someone's inbox.

### Decisions

| Question | Choice |
|---|---|
| Who escalations go to | By **name** per tenant in `handoffs.toml` (Suryodaya: Meera Kulkarni, the only assignable person), resolved to a party id through `escalations.assignees` on every run; nothing hard-coded |
| A tenant with no assignable person (Keystone) | **Decline**: prepared, not filed, with the reason; graded as correct |
| After grading | **Auto-withdraw** the run's escalations and close their sessions, after the "after" ground truth is captured; `--keep-writes` and a manual `agentkit.harness.cleanup` for the rest |

### Added / changed

| What | Where |
|---|---|
| `TaskDef.allow_writes`; `run_one --write`; `batch --write [--keep-writes]`. A run writes only with both the flag and the task opt-in, live only (refused with `--sim`) | `agentkit/harness/tasks.py`, `run_one.py`, `batch.py` |
| Write allowlist: `tool_calls_policy` fails a write run that calls a tool outside the pack's `extras["write_tools"]`; the `sneaky_write` mutant now applies to write runs too | `agentkit/harness/checks.py`, `grade.py`, `mutants.py` |
| Clean-up hook: the batch calls the pack's `extras["cleanup"]` after the "after" capture → `cleanup.json`; `python -m agentkit.harness.cleanup --tenant t [--dry-run]` or `--run-dir` | `agentkit/harness/batch.py`, `agentkit/harness/cleanup.py` |
| `raise_handoffs` files for real: AgentSession (titled with the subject) → `escalations.raise` to the resolved assignee. Skips a subject that's already open; declines with no assignee or when the name isn't assignable; on a refusal or error, closes the session and fails the node | `packs/designreview/handoffs.py`, `handoffs.toml` (`[assignees]`) |
| `cleanup_escalations`: withdraw (with `expect_status`) and close the session; only our `T21-DR ` subjects, and only open or acknowledged ones | `packs/designreview/handoffs.py` |
| Probes `escalations:<file_id>` (ours, any status) and `assignees` | `packs/designreview/probes.py` |
| `handoffs_match_platform` judges filing against those probes. A dry run must leave no new escalation. A write run needs an open `needs_another_app` escalation assigned to the configured, assignable person for every owner, and nothing stray | `packs/designreview/checks.py` |
| `answer_states_handoffs`: a write run's answer must name every filed ESC number; a filing claim with nothing new on the platform fails | `packs/designreview/checks.py` |
| Finding (`escalations`, `already_open`, `filing_errors`, `assignee`), claim rules (ESC numbers, assignee), template and narration for write runs | `finding.py`, `claims.py`, `templates.py`, `prompts/narrate.md` |
| Mutants `escalation_not_on_platform`, `filed_number_unstated` (write runs) | `packs/designreview/mutants.py` |
| t08 and t09 set `allow_writes = true` (still dry runs without `--write`) | `packs/designreview/tasks/` |
| Fixtures re-captured (the two new probes) | `packs/designreview/fixtures/` |
| Reference tests (16 new, against an in-memory writable platform); `tests/` templates updated | `tests_local/test_write_mode.py`, `tests/test_harness.py`, `tests/test_handoffs.py` |

### First live write (2026-09-30, batch `20260930T212448`, LLM answers)

| | Result |
|---|---|
| t08 (Suryodaya) | **pass**. Filed **ESC-2026-00059** (manufacturing: bend-radius cracking, weld access) and **ESC-2026-00060** (quality: PPAP level 3), both open, `needs_another_app`, assigned to Meera Kulkarni, due in 24 h. The answer names both numbers and the assignee |
| t09 (Keystone) | **pass**. Nothing filed (no hand-off needed) |
| Evidence → clean-up | `ground_truth_after.json` shows both open. Then both were **withdrawn** and both sessions **closed** (re-read from the platform); `cleanup --dry-run` finds nothing of ours still open |
| Journal | writes: 2× `AgentSession.create`, 2× `escalations.raise` (all allowlisted) |
| Calibration (write batch) | OK: both write-run mutants caught; `tool_calls_policy` exercised in write mode |
| `tests_local` offline | 494 passed |

Writing the tests exposed one verifier gap, now fixed. When the configured person isn't assignable on the
platform, declining is correct, but the verifier demanded an escalation anyway. It now reads who is assignable
as its own ground truth.

## 2026-09-30 — Dry-run hand-offs and the project schedule (Part 2)

### Why

The seat can see a problem that it can't fix. The Battery Tray's release is blocked by a critical
"2.0 mm inside bend radius — cracking risk" finding, and PPAP level 3 is required. Both need teams whose tools
this seat doesn't have (manufacturing, quality). The agent used to report the blocker and stop. Now it says who
needs to act, cites the exact items, and shows the escalation it *would* file. In a dry run it files nothing,
and every part of that is graded against the platform.

The platform also doesn't flag late work: the Battery Tray's "DFM review closed" milestone was due 2026-09-19
and still reads `in_progress`. The finding computes lateness from the date.

### Added

| What | Where |
|---|---|
| **Generic write preview.** A write action may register `preview`. In a dry run the engine calls it instead of the action, and the node ends `handed_off` with `{"would_file": …, "filed": false}` (`resolved` if the preview is empty; still `declined` without a preview). `record_steps` keeps a hand-off's data | `agentkit/registry.py`, `agentkit/graph/engine.py`, `agentkit/agents/planned.py` |
| Hand-off topics as data: owner (manufacturing / quality / purchasing), ask, whole-word keywords; validated by pydantic | `packs/designreview/handoffs.toml` |
| Actions `list_milestones`, `find_cross_seat_dependencies` (reads) and `raise_handoffs` (write, preview-only: the `AgentEscalation.create` call per hand-off) | `packs/designreview/handoffs.py` |
| Plan `release_handoff`: gate + feedback + milestones in parallel → cross-seat match (settled join) → a rule adds `raise_handoffs` only when something is proposed | `packs/designreview/plans/release_handoff.toml` |
| Finding sections `schedule` (past_due computed from the snapshot date) and `handoffs` (proposed, would_file, filed); sections whose nodes aren't planned are left out | `packs/designreview/finding.py` |
| Claim rules: name each owner, say the hand-offs are only proposed, say "no other team" when none is needed, name each past-due milestone; contradictions for "I escalated / has been filed / was notified" in a dry run and "on track" while late | `packs/designreview/claims.py` |
| Template and narration sections "Schedule" and "Who needs to act" | `templates.py`, `prompts/narrate.md` |
| Probe `milestones:<project_id>`; the release-gate probe now records blocker ids | `packs/designreview/probes.py` |
| Verifiers `handoffs_match_platform`, `schedule_matches_db`, `answer_states_handoffs` (own matchers; drift-aware) | `packs/designreview/checks.py` |
| 10 mutants: invented / dropped / closed-item / marked-filed hand-off, filing claimed, owner unnamed, "no hand-off" unstated, late milestone hidden, date shifted, "on track" claimed | `packs/designreview/mutants.py` |
| Task `t08_battery_tray_handoff` (Suryodaya): two hand-offs, one past-due milestone | `packs/designreview/tasks/` |
| Task `t09_keystone_no_handoff_late_schedule` (Keystone): the negative case — no hand-off is right, three milestones are late | `packs/designreview/tasks/` |
| Both fixtures re-captured (milestone reads) | `packs/designreview/fixtures/` |
| Reference tests (72 new) and the `tests/test_handoffs.py` to-do template; the `test_dag_engine` template covers the preview | `tests_local/test_handoffs.py`, `tests/` |

Nothing in `agentkit/` names a team, a tool or a tenant: the preview is a generic write-action hook, and the
owners, topics and escalation tool are pack data.

### Results

| Run | Result |
|---|---|
| Live batch, LLM answers, both tenants | **9/9 pass** |
| Calibration (live batch) | OK: all 29 mutants caught, no check unexercised |
| Offline batch, per-tenant fixtures | 9/9 pass |
| `tests_local` offline / `tests/` | 478 passed / 4 passed |

The claim audit caught a real invention on t08's first LLM draft (an unsupported "15 mm") and the rewrite fixed
it. Writing the tests also exposed two verifier weaknesses, both fixed:
- "Proposed" in the hand-off's ask text ("propose the process change") no longer counts as saying the hand-off
  wasn't filed.
- `answer_states_handoffs` now reports drift (not fail) when a milestone or item changed during the run.

### Not done here (Part 3)

Filing for real: `raise_handoffs` in write mode (behind `--write` and a task-level `allow_writes`), an
AgentSession for the escalation, `escalations.raise` with a named assignee (needs your choice of assignee),
de-duplication against open escalations, and a `--cleanup` withdraw. The first live write waits for your
go-ahead.

## 2026-09-30 — Run viewer (read-only); claim-audit timestamp fix

### Why

Every run already leaves a full audit trail on disk: answer, finding, graph, tool journal, ground truth
before and after, score and cost. But reading it meant opening nine JSON files per run. The viewer puts
that trail on one local page. It's useful for explaining a graded run and for finding out quickly why a
check failed or drifted.

### Added

| What | Where |
|---|---|
| Run viewer: standard-library HTTP server, JSON API (`/api/batches`, `/api/runs`, `/api/batches/<id>`, `/api/runs/<id>`), plain HTML/JS/CSS page with no framework and no build step | `agentkit/viewer/` (`python -m agentkit.viewer`) |
| Pages: batches; all runs; batch (task × tenant, failing checks, calibration per mutant, `report.md`); run tabs Answer · Finding · Checks · Evidence (graph timeline) · Tool calls · Ground truth (drift diff) · Cost | `agentkit/viewer/static/` |
| Safety: GET/HEAD only (405 otherwise), strict id pattern and folder confinement, static whitelist, text-only rendering, `\u`-escaped JSON, CSP and nosniff headers, binds to 127.0.0.1 | `agentkit/viewer/server.py` |
| Explainer; `tests/test_viewer.py` to-do template; README section | `docs/explainers/agentkit_viewer.md`, `tests/`, `README.md` |
| Reference tests (38 new): API over hand-built and offline-batch run folders, traversal cases, read-only HTTP, headers, hostile-text escaping | `tests_local/test_viewer.py` |

### Fixed

| What | Where |
|---|---|
| The claim audit treated every number in the finding as supported, **including the digits of timestamps and UUIDs**. With a snapshot taken at 12:05, an invented "12 mm" passed the audit. Dates, clock times and UUIDs are now removed before numbers are collected. A regression test pins the clock | `agentkit/answer/claim_audit.py` (`known_numbers`) |

### Results

| Run | Result |
|---|---|
| `tests_local` offline | 406 passed (includes the viewer tests; the previously time-dependent audit test is now stable) |
| `tests/` (your suite) | 4 passed |
| Offline batch, per-tenant fixtures | 7/7 pass |
| Viewer on the live `runs/` folder in Chrome | batches, batch, run tabs render; no console errors; POST → 405; `..%2F.env` → 404 |

## 2026-09-30 — Keystone tasks; generic version selection; per-tenant fixtures

### Why

A check of both AgentSwitch tenants found no new design-review data since 2026-09-28, but showed that
**Keystone** (seeded 2026-09-16) has 21 files with 2–6 revisions and real DFM standards, while every graded
task ran only on Suryodaya. Keystone's version notes also exposed two shapes the task set didn't cover:

- **Boilerplate notes with rev labels** — "Revision C — drawing and model updated together". The right answer
  reports exactly that and invents no specific changes.
- **Real engineering notes without rev labels** — "DFM fixes: increased draft angles, added rib
  reinforcement". The question can only be "what changed in the latest revision", so the verifiers must be
  able to compare the two newest versions rather than named revs.

### Added

| What | Where |
|---|---|
| Task `t06_keystone_boilerplate_notes` — Hydraulic Manifold Mount rev B→C (Keystone) | `packs/designreview/tasks/t06_keystone_boilerplate_notes.toml` |
| Task `t07_keystone_latest_dfm_fixes` — Auger Drive Housing Cover, latest revision (Keystone) | `packs/designreview/tasks/t07_keystone_latest_dfm_fixes.toml` |
| Verifier `answer_reflects_change_notes` — the answer must carry the distinctive words of the compared version's note (default at least 3; all of them when the note has fewer); `skip` when the note has none; `drift` when the note was rewritten during the run | `packs/designreview/checks.py` |
| `select_versions()` — one generic rule for which versions a check compares: `from_rev`/`to_rev` (rev letters or numbers from commit messages, including "Revision B") or `latest = true` (the two newest versions) | `packs/designreview/checks.py` |
| Mutant `change_notes_ignored` — strips the note's words from a passing answer; `answer_reflects_change_notes` must fail | `packs/designreview/mutants.py` |
| Keystone platform fixture (16 exchanges) | `packs/designreview/fixtures/keystone.json` |
| Reference tests for all of the above (15 new) | `tests_local/` |

### Changed

| What | Where |
|---|---|
| `revision_delta_matches_db` and `answer_states_change` use `select_versions()` instead of requiring rev letters | `packs/designreview/checks.py` |
| `answer_reflects_change_notes` added to the existing rev-diff tasks t01, t04, t05 | `packs/designreview/tasks/` |
| Fixture capture runs only the tasks whose `tenants` include the tenant being captured (it used to run every task against it) | `agentkit/sim/capture.py` |
| `--sim` accepts the fixtures **folder**: each task replays `<its tenant>.json`; a single file still works; a missing tenant fixture is an error that says how to capture it | `agentkit/harness/batch.py` (`SimSession`) |
| Suryodaya fixture re-captured (the seat gained two tools, `events.catalog` and `tools.preview`) | `packs/designreview/fixtures/suryodaya.json` |
| README, explainers (`packs_designreview`, `agentkit_sim`, `agentkit_harness`, `capstone_agent`, `capstone_evals`), `tests/README.md` and the `test_verifiers` / `test_sim_platform` to-do templates | `README.md`, `docs/`, `tests/` |

Nothing in `agentkit/` names Keystone or a design-review entity: the tenant is task data (`tenants = ["keystone"]`)
and the version rule is a verifier parameter.

### Results

| Run | Result |
|---|---|
| Live batch, LLM answers, both tenants | **7/7 pass** (t06: 16/16 checks; t07: 16/16 checks) |
| Offline batch, per-tenant fixtures | **7/7 pass** in about a second |
| Calibration (live and offline batches) | OK — every mutant caught, no check unexercised; `change_notes_ignored` caught on all 5 runs it applies to |
| `tests_local` offline | 365 passed |

What the LLM answered for t06: *"The commit messages for rev B and rev C both state only that 'drawing and
model updated together,' so the specific design intent of the rev C change cannot be determined from the
platform data."*

### Noticed, not changed

- Keystone `DF-2026-00026` has `status = approved`, yet release readiness reports "The file is not in the
  canonical ready state" — a possible platform inconsistency; confirm against the DesignFile state machine
  before reporting it.
- Suryodaya milestones carry `created_at` equal to their due dates (two in the future: 2026-10-03, 2026-10-17)
  — seed data with synthetic timestamps; a possible data-integrity report.
