# `core/harness.py` — the uniform run record

*Reading order: **5th**. Depends on nothing project-specific (pure stdlib). Read this right before `capstone_agent.py`, since `TaskRun` is the type that file builds and returns.*

## 10,000-ft view

Two dataclasses (`Step`, `TaskRun`) and one `Protocol` (`Harness`). The entire point of this ~90-line file is a single idea: **whatever the agent does, it produces one JSON-shaped object, and that object gets written to disk before anyone scores it.** Nothing here knows about MCP, LLMs, or design review — it's the generic contract that any future harness (not just `capstone_agent.py`) could conform to.

## Why it's written this way (intent & trade-offs)

- **A `Protocol`, not an abstract base class, for `Harness`.** `Team21DesignReviewAgent` in `capstone_agent.py` never imports or subclasses `Harness` — it just happens to have a matching `run(task_id, prompt) -> TaskRun` method, and that's enough (structural typing). The trade-off: nothing enforces conformance at class-definition time — a typo in the method name or signature would only surface when something tries to use the object *as* a `Harness`, not when the class is defined. This was accepted because it keeps `capstone_agent.py` from needing to import a base class just to satisfy a shape.
- **The raw run is saved *before* scoring, deliberately decoupled from `core/eval_framework.py`.** This is the file's whole reason for existing, ported from `S18Code`'s pattern: a `TaskRun` is a complete, self-contained record of what happened, independent of any particular scoring logic. The trade-off/benefit: if a scoring axis has a bug, you fix the axis and re-run `rescore.py` against the *same* saved evidence — you never need to re-run the (real, live, costs-money) agent just because the grader was wrong. This only works because `TaskRun` doesn't bake in anything scorer-specific.
- **`Step` is intentionally generic (`kind`, `target`, `ok`, `detail`), not typed per event kind.** A `Step` could describe a DAG node's outcome, an LLM call, or the final answer — see `capstone_agent.py`'s three different `kind` strings (`"tool_call"`, `"llm_call"`) used against this one shape. The trade-off: `detail: Any` means nothing type-checks what's actually inside a step's detail — a consumer has to know, out-of-band, that a `"tool_call"` step's `detail` is whatever an MCP tool returned (or an error string) while an `"llm_call"` step's `detail` is only populated on failure. A more rigid design (a `Step` subclass per kind) would catch a misuse at type-check time; this design trades that safety for not having to define N step types for N event kinds that may never grow much beyond two.
- **`TaskRun.save()` names the file `{task_id}_{ts}.json`, with `ts = int(self.started_at)` (second precision).** Two runs of the *same task* within the same second would collide and overwrite each other. This is an accepted (if unexamined) risk given this project's actual usage pattern — one run at a time, manually invoked — but would be a real bug in an automated harness firing many runs per second.
- **`TaskRun.load()` reconstructs `Step` objects with `Step(**s)`**, meaning the saved JSON's step dicts must have exactly the fields `Step` expects (`kind`, `target`, `ok`, `detail`) — an old saved-run file from a version of this file with a different `Step` shape would fail to load with a `TypeError`, not a graceful migration. There is no schema-versioning in `TaskRun`'s serialization the way `GraphStore._checkpoint` has a `FORMAT` constant — this is a real gap between the two "save state to JSON" mechanisms in this project.

## What it does (walkthrough)

1. **`Step`** — a flat record of one thing that happened.
2. **`TaskRun`** — the whole run:
   - `add_step(...)` is the only way `steps` grows; it's a thin convenience wrapper (`self.steps.append(Step(...))`), used repeatedly by `capstone_agent.py` (once per DAG node, once for the synthesis LLM call).
   - `to_dict()` is a manual, explicit dict-builder (not `dataclasses.asdict(self)` directly on `TaskRun`, though it *does* use `asdict(s)` for each nested `Step`) — this means adding a new field to `TaskRun` requires remembering to also add it here, which `dataclasses.asdict` on the whole object wouldn't require. This is a real maintenance footgun worth flagging.
   - `save(proofs_dir)` — creates the directory if needed, writes `to_dict()` as indented JSON, returns the `Path` it wrote to (so a caller like `capstone_evals.py` can pass that path straight into `EvaluationResult.run_path` without re-deriving it).
   - `load(path)` (a `@classmethod`) — the inverse of `save`, used by `rescore.py` to reconstruct a `TaskRun` from disk without re-running anything.
3. **`Harness`** — the `Protocol`. Not instantiated, not subclassed anywhere in this codebase today — it exists as documentation-as-code for what "a thing this project's eval tooling can score" looks like.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `Step.kind` | `str` | Free-form category tag (`"tool_call"`, `"llm_call"`, or the documented-but-unused `"answer"`). Not an `Enum` — a typo here (e.g. `"tool-call"` with a hyphen) would silently create a new, inconsistent category rather than raising an error. |
| `Step.target` | `str` | What the step acted on — a DAG node id or a model/synthesis label. |
| `Step.ok` | `bool` | Pass/fail for this one step. |
| `Step.detail` | `Any` | Free-form payload; a tool's result dict, an error string, or `None`. |
| `TaskRun.task_id` | `str` | Matches the id used in `tasks/*.json` and in `proofs/runs/` filenames. |
| `TaskRun.prompt` | `str` | The exact prompt this run answered — kept alongside the run so a saved file is self-describing without needing to cross-reference `tasks/`. |
| `TaskRun.steps` | `List[Step]` | Chronological (insertion-ordered) record of everything the run did. |
| `TaskRun.claimed_answer` | `Optional[str]` | The agent's final synthesized text — `None` until the synthesis call succeeds (or forever, if it fails). This is exactly the field every ground-truth axis in `core/eval_framework.py` reads. |
| `TaskRun.ended` | `str` | One of `"done"` / `"error"` in current usage; `"no_ready_nodes"` is documented in the type comment but never actually set anywhere in this codebase — see "tests to add" below. |
| `TaskRun.error` | `Optional[str]` | Set only on the synthesis-call failure path in `capstone_agent.py`; `None` otherwise. |
| `TaskRun.seconds` | `float` | Wall-clock duration for the whole run, set once at the very end by the caller (not computed internally by `TaskRun` itself). |
| `TaskRun.started_at` | `float` (`default_factory=time.time`) | Set automatically at construction — this is what timestamps the saved filename. |

## Function/method reference

| Function/Method | Inputs → Output | Notes |
|---|---|---|
| `TaskRun.add_step(kind, target, ok, detail)` | → `None` | Pure mutation, no return value. |
| `TaskRun.to_dict()` | → `Dict[str, Any]` | Manually enumerates every field — see the maintenance-footgun note above. |
| `TaskRun.save(proofs_dir)` | `Path` → `Path` | Side effect: writes a file, creating `proofs_dir` if missing. Second-precision filename collision risk (see above). |
| `TaskRun.load(path)` (classmethod) | `Path` → `TaskRun` | Raises `KeyError` if the JSON is missing `task_id`/`prompt` (no `.get()` fallback for those two — unlike every other field, which uses `.get(..., default)`), and `TypeError` if a step dict has unexpected keys. |
| `Harness.run(task_id, prompt)` | → `TaskRun` | Protocol method signature only — no implementation, no default. |

## How to test it

Entirely pure, in-memory, filesystem-only I/O (no network, no LLM) — this should have the easiest 100%-coverage test file in the whole project.

```python
def test_add_step_appends():
    run = TaskRun(task_id="t", prompt="p")
    run.add_step("tool_call", "get_file", True, {"id": "x"})
    assert len(run.steps) == 1
    assert run.steps[0].kind == "tool_call"

def test_save_then_load_round_trips(tmp_path):
    run = TaskRun(task_id="t1", prompt="p")
    run.add_step("llm_call", "synthesis", True)
    run.claimed_answer = "the answer"
    run.seconds = 1.23
    path = run.save(tmp_path)
    loaded = TaskRun.load(path)
    assert loaded.task_id == run.task_id
    assert loaded.claimed_answer == run.claimed_answer
    assert loaded.steps[0].kind == "llm_call"

def test_save_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "dir"
    run = TaskRun(task_id="t2", prompt="p")
    path = run.save(target)
    assert path.exists()

def test_two_saves_in_the_same_second_collide(tmp_path):
    run1 = TaskRun(task_id="same", prompt="p")
    run2 = TaskRun(task_id="same", prompt="p")
    run2.started_at = run1.started_at  # force the same second
    p1 = run1.save(tmp_path)
    p2 = run2.save(tmp_path)
    assert p1 == p2  # documents the known collision risk, doesn't fix it
```

## Tests that should be added for stability

In priority order:

1. **The round-trip test above doesn't exist yet** — `save()`/`load()` symmetry is the single most load-bearing property in this file (everything in `rescore.py` depends on it) and has zero test coverage today.
2. **A test — and probably a fix — for the filename collision risk.** Either add sub-second precision (`time.time()` has it; the current code discards it with `int(...)`) or add a random suffix/UUID to the filename, and write a test proving two runs of the same task never overwrite each other.
3. **A test for `TaskRun.load()`'s behavior on a malformed/partial file** (missing `steps` key, a step dict with an extra key, a completely empty JSON object `{}`) — right now the failure mode is whatever exception `.load()` happens to throw, untested and unspecified.
4. **A test (or a decision) for the unused `"no_ready_nodes"` `ended` value.** Either wire `core/dag_engine.py`'s "stuck" break path through to actually set `run.ended = "no_ready_nodes"` in `capstone_agent.py`, or remove the dangling reference from this file's docstring/type comment — right now it documents behavior that doesn't exist.
5. **A test that `to_dict()` stays in sync with `TaskRun`'s actual fields** — e.g. a test that iterates `TaskRun.__dataclass_fields__` and asserts every field name appears as a key in `to_dict()`'s output, so adding a field to the dataclass without updating `to_dict()` fails CI immediately instead of silently dropping data from every saved run.
