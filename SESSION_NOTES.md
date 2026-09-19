# Team 21 (Design Review) — Complete Session Summary & Reference Notes

> **Date:** September 18, 2026  
> **Seat:** Design Review (`designreview`) — Team 21  
> **Platform:** AgentSwitch (Suryodaya Precision Works & Keystone Precision Works LLC)  
> **Core Benchmark Prompt:** *"What changed between rev B and rev C, and are there manufacturability problems in this part?"*

---

## 📌 1. Key Discoveries & Platform Analysis

### 1.1. Seed Data Reality
* Out of 100 seeded reviews in the database, only **5 represent real, coherent engineering reviews** (e.g., `DR-2026-00001` Battery Tray Rev C, `DR-2026-00003` Pump Bracket Rev B, `DR-2026-00004` Bench Vice Rev 3). The remaining 95 are synthetic supply-chain filler rows.
* Only **9 files have multi-revision histories** (27 `DesignVersion` rows total), with the **Bharat EV Battery Tray Assembly (`DF-2026-00001`)** having the full 3-version chain (`Rev A` ➔ `Rev B` ➔ `Rev C`).

### 1.2. Platform & CAD Gaps
* **CAD Kernel Gaps:** `analysis/thickness` and `analysis/interference` return `501 Not Implemented: needs OpenCascade CAD kernel`. CAD files have `has_brep: 0`.
* **AI Review Gap:** `ai-review` / `DesignAICheck` returns `501 Not Implemented`. Our autonomous agent *is* the AI reviewer.
* **Diff Source of Truth:** `diff_from_parent_json` is `null` across all version rows. Engineering changes must be synthesized from:
  1. `DesignVersion.commit_message` (e.g. *"corner bend radius opened 2.0 to 3.0 mm to stop micro-cracking; blank length grows 4.2 mm"*),
  2. Derivative flat patterns (`DF-2026-00002` Base Pan DXF),
  3. Tooling updates (`DF-2026-00005` Weld Fixture locators moved for Rev C flange),
  4. BOM tree structures (`assembly_tree_json`),
  5. `DesignFeedback` defect logs and `DesignChecklist` rules.

---

## 🐛 2. Bugs Filed by Team 21

Retrieved via `GET /api/bug-report/mine`:

1. **Bug ID:** `fb28c793-d3a4-4eb3-b5eb-0ddab7bb1b49` (Status: `new`)
   * **Context:** `DesignFile:4e6bf256-9e16-4158-bdf5-633b4bc2ecb4` (*ST-VICE-150 Bench Vice Assembly*)
   * **Issue:** Clicking "Feedback (8)" crashes the UI with `TypeError: e.tags.split is not a function` in component `DS`.
2. **Bug ID:** `1b711d71-a408-4dc4-aff0-a26c30cc25cb` (Status: `new`)
   * **Context:** `DesignReview:Home`
   * **Issue:** Missing front-end and back-end date validation allows creating/saving Design Reviews with a past Due Date (e.g. `18 Jan 2026` on record created `18 Sept 2026`).

---

## 🏛️ 3. Architecture & Modular Codebase Structure

The project uses pure Python foundation libraries (`networkx`, `httpx`, `pyyaml`) --
zero LangChain, LangGraph, or CrewAI. `core/` was upgraded from a first-draft
version (static task list, prose-keyword scoring, fixed-order gateway fallback)
to patterns ported from the user's own prior EAGv3 coursework under
`D:\sjk\eagv3` -- each module's docstring names its source. See the plan at
`C:\Users\ANT-PC\.claude\plans\look-at-d-sjk-eagv3-projects-whimsical-zephyr.md`
for the full survey and scope decisions (what was ported, what was deliberately
cut, and why).

```text
c:\sjk\eagv3\capstone\designreview\
│
├── core/                                 # 🧩 REUSABLE CORE FRAMEWORK
│   ├── __init__.py                       # Package exports
│   ├── llm_gateway.py                    # Rate-aware routing + retry + cache + cost ledger (from glc_v5)
│   ├── routing.yaml                      # Declarative provider tiers/order (from glc_v5's routing.yaml)
│   ├── economics.py                      # Cost/token ledger (trimmed from glc_v5's economics/)
│   ├── dag_engine.py                     # Event-sourced live graph + ready-set scheduler (from S17Code)
│   ├── harness.py                        # Harness protocol + Step/TaskRun raw-run record (from S18Code)
│   ├── eval_framework.py                 # Ground-truth-separated axes engine (from S18Code)
│   └── judge.py                          # Single-pass LLM-as-judge for qualitative axes (from S17Code)
│
├── tasks/                                # Task definitions as data (prompt + target entity)
│   ├── t01_battery_tray_revc_dfm.json    # The real, working benchmark task
│   └── t02_refusal_stub.json             # STUB -- must be hand-authored, see PLAN.md \u00a77
│
├── proofs/
│   ├── runs/                             # Raw TaskRun JSON, written BEFORE scoring
│   ├── checkpoints/                      # Live-graph JSON checkpoint per run
│   └── results.json                      # rescore.py's output
│
├── capstone_agent.py                     # 🎯 TEAM 21 CAPSTONE AGENT (live-graph DAG + LLM synthesis)
├── capstone_evals.py                     # 📊 GROUND-TRUTH EVAL SUITE (re-fetches platform state, doesn't trust agent prose)
├── rescore.py                            # Re-score saved runs with zero LLM calls (from S18Code)
├── as_client.py                          # 🔌 PLATFORM MCP & REST TRANSPORT
├── requirements.txt                      # httpx, networkx, python-dotenv, pyyaml
│
├── ENTITY_SCHEMAS.md                     # 📜 Complete 24-entity schema & state machine definitions
├── GAP_REPORT.md                         # 📝 Week 1 Deliverable (Competitor Gap Analysis vs CoLab)
├── links.md                              # 🔗 Annotated list of explored application routes
├── secrets.local.txt                     # 🔐 Local credentials (git-ignored)
└── README.md                             # 📖 Repository introduction & quickstart
```

---

## 🧪 4. The Ground-Truth Evaluation Suite

`capstone_evals.py` runs the Section 8 prompt and scores the result with axes
that **re-fetch real platform state live** and check the agent's claim
against it -- not axes that grep the agent's own prose in isolation (that was
the first draft's weakness, and PLAN.md \u00a77's grading bar rules it out
explicitly: "verifiers that read the database directly... not ones that trust
the agent's own prose output"). Confirmed live 2026-09-18: a fabricated claim
("bend radius changed 5.0mm\u21929.0mm, ready for release") is correctly
rejected by `cites_real_bend_radius_change` and `matches_real_release_readiness`
even though nothing about the prose itself looks wrong.

| Axis | Ground truth re-fetched | Real result (2026-09-18) |
| :--- | :--- | :--- |
| `cites_real_bend_radius_change` | `DesignVersion.list` commit messages | **PASS** |
| `cites_real_blank_growth` | `DesignVersion.list` commit messages | **PASS** |
| `matches_real_release_readiness` | `endpoint.designreview.release_readiness` | **PASS** |
| `did_not_fabricate_geometry_diff` | n/a (static guard: `diff_from_parent_json` is null in this build) | **PASS** |
| `judged_reasoning_quality` | LLM-judge rubric (`core/judge.py`) -- the one qualitative axis | **PASS** |

Real ground truth on the Bharat EV Battery Tray Assembly
(`39b69109-b56a-4bf4-af48-1ecf2b18f8a6`): `endpoint.designreview.release_readiness`
currently returns `ready: false` with a critical open blocker on the same
bend-radius finding the Rev C commit message addresses.

`rescore.py` re-runs just the scoring (re-fetching ground truth, but zero LLM
calls unless `--with-judge` is passed) against saved `proofs/runs/*.json` --
a scoring bug is a free fix, not a re-run of the paid agent.

---

## ⚡ 5. Quick Reference Commands

### Test LLM Gateway (rate-aware routing + retry + cache + cost ledger):
```powershell
python core/llm_gateway.py
```

### Run the Autonomous Agent (writes a TaskRun to proofs/runs/):
```powershell
python capstone_agent.py
```

### Run the Ground-Truth Evaluation Suite:
```powershell
python capstone_evals.py
```

### Re-score Saved Runs Without Re-running the Agent:
```powershell
python rescore.py
python rescore.py --with-judge   # also runs the LLM-judge axis
```

### Test Platform Authentication & MCP Tool Discovery:
```powershell
python as_client.py suryodaya
python as_client.py keystone
```
