# Graded test suite — how to write it

**Rule from the brief (PLAN.md §7): tests in this folder must be written by hand.
A test authored by an LLM scores zero.** Every `test_*.py` here is a to-do list:
a docstring of use cases, the imports you'll need, and `# Write your tests below.`
Tick `Written by hand: [x] yes` and fill in `Author(s)` when you start a file.

`tests_local/` (git-ignored) is a separate, AI-written reference suite used to
develop the code. Don't copy from it; it's there to show that each behaviour
listed in the templates is real and testable.

## Run

```
python -m pytest                                  # this folder (pytest.ini: testpaths = tests), offline tests
python -m pytest -m "live and not writes"         # live, read-only platform tests (needs .env)
python -m pytest -m "live and writes"             # live WRITE tests (creates T21-TEST records)
python -m pytest -m agent                         # full agent runs (costs LLM calls)
python -m pytest -m gateway_live                  # one real call per LLM provider
```

Markers (pytest.ini): `live`, `agent`, `keystone`, `writes`, `gateway_live`.
Anything without a marker must run offline: no network, no LLM, no `.env`.

## Which template covers what

| File | Code under test | Offline? |
|---|---|---|
| `test_as_client.py` | `as_client.py` (compatibility client) | yes |
| `test_seat_rpc.py` | `agentkit/transport/*` (MCP client, REST reader, tool journal) | yes |
| `test_llm_gateway.py` | `agentkit/llm/*` (routing, retries, cost ledger) | yes |
| `test_dag_engine.py` | `agentkit/graph/*` (live graph, verdicts, rule planner, checkpoints) | yes |
| `test_finding_and_claims.py` | `packs/designreview/finding.py`, `claims.py`, `templates.py`, `agentkit/answer/*` | yes |
| `test_retrieval.py` | `agentkit/retrieval/*`, `gather_dfm_standards` | yes |
| `test_config_models.py` | TOML config / plans / tasks and their pydantic models | yes |
| `test_harness.py` | run record + grader | yes |
| `test_verifiers.py` | `packs/designreview/checks.py` (the graded verifiers) | yes |
| `test_sim_platform.py` | `agentkit/sim/*` + the whole batch offline on the captured fixture | yes |
| `test_calibration.py` | verifier self-test (`agentkit/harness/mutants.py`) | yes |
| `test_modularity.py` | architecture guards (domain-free framework, no agent frameworks) | yes |
| `test_platform_live.py` | platform facts the tasks rely on | live |
| `test_platform_workflows.py` | review / feedback workflows | live + writes |
| `test_agent_tasks.py` | the seven tasks (t01–t05 Suryodaya, t06–t07 Keystone), end to end | live + agent |

## Tools you can use (all product code, not test code)

- **No network for MCP:** a fake `Wire` — any object with
  `send(method, url, headers, body, *, safe_to_repeat) -> WireReply(status, body_bytes)` —
  passed as `SeatSession(TenantLogin(...), wire=fake)`.
- **No network for LLMs:** `LLMGateway(<routing.json>, transport=httpx.MockTransport(handler), sleep=list.append, env={...})`.
- **A whole platform offline:** `SimPlatform("packs/designreview/fixtures/<tenant>.json", faults=[...])` —
  the real captured responses. Faults: `drop_tool:<tool>`, `fail_once:<tool>:<kind>`,
  `fail_always:<tool>:<kind>`, `edit_text:<old>=><new>`. Re-capture with `python -m agentkit.sim.capture --tenant <tenant>`.
  `SimSession("packs/designreview/fixtures", ...)` gives every task its own tenant's fixture.
- **The whole harness offline:** `SimSession(fixture, faults, registry.read_only)` →
  `run_batch(tasks, runs_dir=tmp_path, reader_factory=sim.reader_for, agent_runner=sim.run_agent, fresh_reader_per_task=True)` →
  `grade_batch(batch_dir)`.
- **Verifiers without files:** build `agentkit.harness.checks.RunBundle(...)` by hand with
  `after={"observations": {"versions:<file_id>": {"value": [...]}}}`.
- **Graph without MCP:** a `Registry()` with small fake actions and `RunEnv(client=<object with invoke_tool>)`.

## Writing good tests here

- One behaviour per test; name the test after the behaviour (`test_flaky_read_is_retried_then_resolves`).
- Prove checks **fail** on wrong input, not just pass on right input — that's what the brief grades.
- Live tests: re-read a record before asserting on it (other teams change the shared book), and never
  write to records the team didn't create.
- Keep `tmp_path` for every file a test writes; never write into `runs/`, `proofs/` or `packs/`.
