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

The project has been cleanly refactored without external agent frameworks (zero LangChain, LangGraph, or CrewAI). It uses pure Python foundation libraries (`networkx`, `httpx`, `pydantic`):

```text
c:\sjk\eagv3\capstone\designreview\
│
├── core/                                 # 🧩 REUSABLE CORE FRAMEWORK
│   ├── __init__.py                       # Package exports
│   ├── llm_gateway.py                    # Multi-provider LLM transport & failover
│   ├── dag_engine.py                     # NetworkX topological DAG task orchestrator
│   └── eval_framework.py                 # Multi-axis evaluation & scoring harness (BaseEvaluator)
│
├── capstone_agent.py                     # 🎯 TEAM 21 CAPSTONE AGENT (Design Review DAG & LLM synthesis)
├── capstone_evals.py                     # 📊 TEAM 21 EVALUATION SUITE (5-Axes benchmark scorer)
├── as_client.py                          # 🔌 PLATFORM MCP & REST TRANSPORT
│
├── ENTITY_SCHEMAS.md                     # 📜 Complete 24-entity schema & state machine definitions
├── GAP_REPORT.md                         # 📝 Week 1 Deliverable (Competitor Gap Analysis vs CoLab)
├── links.md                              # 🔗 Annotated list of explored application routes
├── secrets.local.txt                     # 🔐 Local credentials (git-ignored)
└── README.md                             # 📖 Repository introduction & quickstart
```

---

## 🧪 4. The 5-Axes Evaluation Suite

The benchmark evaluator (`capstone_evals.py`) runs the Section 8 prompt and scores against 5 concrete rubric validators:

| Axis | Criteria | Result |
| :--- | :--- | :--- |
| **Axis 1: Geometric Delta** | Detects corner bend radius increase (`2.0mm` ➔ `3.0mm`). | **PASS** |
| **Axis 2: Dimensional Impact** | Detects resulting blank growth (`+4.2mm`). | **PASS** |
| **Axis 3: DFM Root Cause** | Identifies micro-cracking at the flange as the formability issue. | **PASS** |
| **Axis 4: Downstream Tooling** | Identifies weld fixture locator repositioning (`DF-2026-00005`). | **PASS** |
| **Axis 5: Governance / Gating** | Prevents premature release approval (recommends tooling validation first). | **PASS** |

### Benchmark Evaluation Output:
```json
{
  "task_id": "T01_BATTERY_TRAY_REVC_DFM",
  "prompt": "What changed between rev B and rev C, and are there manufacturability problems in this part?",
  "passed": true,
  "score": 1.0,
  "latency_sec": 31.9,
  "feedback_notes": "All benchmark axes passed successfully."
}
```

---

## ⚡ 5. Quick Reference Commands

### Test LLM Gateway:
```powershell
python core/llm_gateway.py
```

### Run Full Benchmark Evaluation Suite:
```powershell
python capstone_evals.py
```

### Run Autonomous Agent Directly:
```powershell
python capstone_agent.py
```

### Test Platform Authentication & MCP Tool Discovery:
```powershell
python as_client.py suryodaya
python as_client.py keystone
```
