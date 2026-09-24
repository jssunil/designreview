# Design Review — Gap Report (Team 21)

> **Seat:** `designreview` | **Focus:** Multi-Revision CAD/BOM Comparison, DFM Analysis & Automated Design Review
> **Last verified against live platform:** 2026-09-24 (Suryodaya + Keystone, MCP `tools/list` = 300 tools per tenant)
> **Primary Benchmarks Researched:**
> 1. **CoLab Software** ([colabsoftware.com](https://www.colabsoftware.com/)) — *AutoReview, AI CAD Review, Lessons Learned agent, AI Knowledge Graph (2026)*
> 2. **Bananaz AI** ([bananaz.ai](https://www.bananaz.ai/)) — *Design Agent, Change Analysis & Collaboration Suite (2026)*
> 3. **OpenBOM** ([openbom.com](https://www.openbom.com/)) — *CAD File Agent & Product Memory (Aras ACE 2026)*
> 4. **NexCAD** ([nexcad.ai](https://nexcad.ai/)) — *AI drawing checker with ASME Y14.5 / ISO compliance and Autofix* (secondary)
>
> Supporting research: see [`research.md`](research.md).

---

## 1. Executive Summary & Competitive Landscape

Our assigned seat (**Team 21 — Design Review**) addresses the core autonomous challenge:
```text
"What changed between rev B and rev C, and are there manufacturability problems in this part?"
```

In the 2026 engineering AI landscape, commercial platforms cover complementary parts of this problem:

1. **Bananaz AI**: Built for mechanical CAD/PDM pipelines (SolidWorks, Creo). Strongest at **3D geometry revision diffing**, **automated FAI ballooning**, **change-by-change sign-offs**, and **Pro vs. Light model routing**.
2. **CoLab Software**: Cloud design-review layer. It has **AutoReview** (an AI peer checker against company standards), **AI CAD Review**, a **Lessons Learned agent**, an **AI Knowledge Graph** of standards/NCRs, markup pinned to model geometry, and PLM integrations (Windchill, Teamcenter, 3DEXPERIENCE, SOLIDWORKS PDM).
3. **OpenBOM**: A **Product Memory** context graph that links revisions, BOMs, where-used and decision history. Its **CAD File Agent** ships today. Its roadmap names **Review Agents** for *engineering-change impact analysis*.
4. **NexCAD**: An in-CAD (Inventor/SolidWorks) AI drawing checker. It validates drawings against ASME Y14.5 / ISO / company standards, auto-fixes what it finds, and learns from reviewer corrections.

CoLab's own research gives the business case. **90%** of companies delay launches because of late design errors, and **60%** of late-stage errors are preventable. Only **56%** of design standards are documented, current *and* actually applied in reviews. Leaders estimate **~75%** of drawing-review work is automatable. They put DFM/assembly review 1–2 years behind that.

---

## 2. What Do Industry Leaders Do That AgentSwitch Does Not?

### 2.1. 3D Geometric Revision Diffing & Change Analysis
* **Industry State (Bananaz / CoLab):** Ingest native CAD models, detect dimensional changes and 3D volume shifts across revisions, and produce side-by-side redline delta packages. CoLab auto-detects revisions through PLM integration.
* **AgentSwitch Gap (re-verified 2026-09-24):**
  * `DesignVersion.parent_version_id`, `file_hash` and `diff_from_parent_json` **are now populated** on all 9 multi-revision files (27 versions). However, `diff_from_parent_json` only holds **hash lineage** (`parent_version_id`, `parent_file_hash`, `file_hash`, `version_number`). It records no semantic, dimensional or feature-level change.
  * `DesignComparison` only diffs the **assembly part list** (`basis: "assembly part list"`, `geometry_compared: false`). The only comparison on the tenant (Bench Vice v2→v3) reports `0 added / 0 removed / 0 modified` even though the commit messages describe a hardened jaw insert, a trapezoidal thread and added ribbing.
  * `endpoint.designreview.compare` has been **removed from the seat's MCP catalogue**. `DesignComparison.create` now expects the caller to *supply* `diff_result_json` and the counts. The platform no longer computes a diff for the agent at all.
  * Revision letters (rev A/B/C) are **not modelled**. Versions are integers (`version_number` 1..n) and the rev letter appears only in `commit_message` free text. The mapping is not consistent either: on the Propeller, rev B is v1, while on the Battery Tray rev B is v2.

### 2.2. Automated Release Artifacts & Inspection Ballooning
* **Industry State (Bananaz / NexCAD):** Parse CAD dimensions, tolerances and GD&T to produce inspection balloons, AS9102/ISO FAI packages and redline change reports. NexCAD also auto-fixes drawing non-conformances in the CAD tool.
* **AgentSwitch Gap:** There are no data models or endpoints for balloon coordinates, FAI packages or inspection sheets. The new `DesignPackage` entity (`draft → in_review → approved → released`) is a file bundle, not an inspection artefact.

### 2.3. Automated AI Design Review Pipeline
* **Industry State (CoLab AutoReview / NexCAD):** Background agents check every upload against company DFM/GD&T standards and drawing rules before a human sees it. They cite the standard they checked against and learn from reviewer corrections.
* **AgentSwitch Gap:** `POST /api/designreview/ai-review` returns `501` (*"needs a model call that has not been wired up"*). Since the 16 Sep survey, **all `DesignAICheck.*` tools have been removed from the seat's MCP catalogue**, so the agent cannot even read historical AI checks. `DesignStandard` rows carry an `ai_embedding_json`, but it is seed placeholder data (`{'generated': True, ...}`) and there is no search endpoint over it.

### 2.4. CAD Geometric Kernel & DFM Compute
* **Industry State (CoLab / Bananaz / Creo AI):** A CAD kernel computes ray-cast wall-thickness distributions, draft angles and assembly interference.
* **AgentSwitch Gap (re-verified 2026-09-24):** `POST /api/designreview/analysis/thickness` and `/analysis/interference` still return `501` (*"needs a CAD kernel (OpenCascade), which this build does not ship"*). `endpoint.designreview.measure` has been **removed from `tools/list`**. [Open CASCADE Technology](https://www.opencascade.com/occt3d-technology/) is LGPL and free for commercial use, so this is a deployment decision, not a licensing blocker.

### 2.5. 3D WebGL Visualization & Spatial Annotation Pinning
* **Industry State (CoLab / Bananaz):** Browser CAD viewers let reviewers pin comments to model faces, save view states, take section cuts and measure. CoLab supports 30+ file types without exporting.
* **AgentSwitch Gap:** `GET /api/designreview/files/:id/gltf` still returns **HTTP 404** (re-verified 2026-09-24), so the 3D viewer is broken. `DesignMarkup`, `DesignViewState` and `DesignSavedView` entities now exist, which gives an agent a *schema* for pinned markup, but no geometry to pin it to. The UI shows **no version-history panel**, so a human reviewer cannot see rev B vs rev C in the UI at all.

### 2.6. Granular Change-by-Change Approvals
* **Industry State (Bananaz):** Reviewers sign off each revision delta separately (e.g. approve the rib stiffener, reject the wall thinning).
* **AgentSwitch Gap:** `DesignReview` has one review-level flow: `draft → open → in_progress → completed` (`start_review`, `begin_work`, `complete_review`). `DesignFeedback` has per-item accept/reject (`submit_for_review`, `accept`, `reject`, `accept_directly`, `reject_directly`), but a feedback item is not tied to a specific *delta* between two versions. *(Note: our filed bug `16497568` and `README.md` describe the review flow as `draft → in_review → approved/rejected`; that is inaccurate. `ENTITY_SCHEMAS.md` has it right.)*

### 2.7. Cross-Project Knowledge Graph ("Product Memory" / Lessons Learned)
* **Industry State (OpenBOM Product Memory / CoLab Lessons Learned agent):** Surface precedents from past designs just in time, e.g. *"have we seen flange micro-cracking on similar sheet-metal trays?"*
* **AgentSwitch Gap:** There is no semantic or cross-project search over `DesignFeedback`, `DesignChecklistResult` or `DesignStandard`. List filters are exact-match or substring only.

### 2.8. Release Blast Radius / Engineering-Change Impact *(new)*
* **Industry State (OpenBOM Review Agents roadmap, Oracle PLM design-to-source):** Before an ECO, quantify impact on BOMs, open work orders, pinned items and demand.
* **AgentSwitch:** This is **partly a strength**. The new `endpoint.designreview.release_impact.get` returns a quantified blast radius: DesignBOMs, BOMs, open work orders, items on old versions and open sales demand. **But the output is currently unreliable** (see §6, candidate bug C-1). Actually releasing (`release_impact` with `action: release`) is gated to `design_admin`, which our `design_user` seat does not hold.

---

## 3. Side-by-Side Feature Matrix

| Feature / Domain Area | Bananaz AI | CoLab | OpenBOM | NexCAD | AgentSwitch Platform (2026-09-24) | Team 21 Agent (today) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **3D Geometric Revision Diff** | ✅ Native | ⚠️ Compare + markup | ⚠️ BOM delta | ❌ | ❌ Hash lineage only; `compare` removed from MCP | ✅ Commit-message delta reasoning |
| **BOM Revision Delta / Where-Used** | ✅ | ✅ | ✅ Context graph | ❌ | ⚠️ `DesignBOM` (2 rows) + `release_impact.get` (unreliable) | ❌ Not used yet |
| **Drawing Ballooning & FAI** | ✅ AS9102 / ISO | ⚠️ Markup | ❌ | ⚠️ Drawing checks | ❌ No models | ❌ |
| **DFM / GD&T Standards Checks** | ✅ | ✅ AutoReview | ⚠️ | ✅ Y14.5 / ISO + Autofix | ❌ `ai-review` 501; `DesignAICheck` gone from MCP | ⚠️ LLM synthesis; ignores `DesignStandard` |
| **Thickness / Clash Kernel** | ✅ | ✅ | ❌ | ❌ | ❌ 501 (no OpenCascade) | ✅ Refuses to fabricate |
| **3D Spatial Feedback Pinning** | ✅ | ✅ | ⚠️ Viewer | ❌ | ❌ glTF 404 (`DesignMarkup` schema exists) | ❌ |
| **Change-by-Change Sign-off** | ✅ | ⚠️ Thread | ⚠️ Item | ❌ | ⚠️ Per-feedback accept/reject only | ❌ Read-only agent |
| **Lessons Learned / Precedents** | ⚠️ | ✅ Agent | ✅ Product Memory | ⚠️ Learns fixes | ❌ No cross-project search | ⚠️ Same-file feedback only |
| **Release Readiness Gate** | ⚠️ | ⚠️ | ⚠️ | ❌ | ✅ `release_readiness` (real blockers) | ✅ Uses it |
| **Tiered Model Routing** | ✅ Pro/Light | ⚠️ | ⚠️ | ⚠️ | ❌ | ✅ `LLMGateway` tiers |
| **Autonomous Multi-step Orchestration** | ⚠️ Human in loop | ⚠️ Human in loop | ⚠️ CAD File Agent | ❌ | ⚠️ MCP only | ✅ Live DAG + dynamic patching |

---

## 4. Which Gaps Can Our Agent Close vs. Which Require Platform Work?

### 🔴 Platform Gaps (the platform team's problem)
1. **CAD kernel:** thickness, interference, measure and true geometry diff all need OpenCascade (or similar) deployed.
2. **Semantic revision diff:** `diff_from_parent_json` should carry feature/dimension/property deltas, not just hashes. `DesignComparison` should compare more than part lists. Revision letters should be a first-class field.
3. **glTF asset route:** map `/api/designreview/files/:id/gltf` to `DesignVersion.gltf_file`, and add a version-history panel to the UI.
4. **AI review:** wire `ai-review` and restore read access to `DesignAICheck` over MCP.
5. **FAI / ballooning data models**, delta-level sign-off, and cross-project semantic search.
6. **Data integrity:** fix the `release_impact` attribution and `released_version_id` referential integrity (§6).

### 🟢 Orchestration We Can Build Over Today's Tools
1. **Revision delta from the version chain:** read `DesignVersion.list`, map rev letters to version numbers from `commit_message`, and extract the concrete deltas (2.0 → 3.0 mm bend radius, 4.2 mm blank growth). Where `diff_from_parent_json` shows different hashes, cite that as proof the file changed; the text supplies the *what*. *(Done for the Battery Tray; must be generalised.)*
2. **Derivative-file propagation:** follow a change into dependent files, e.g. the Base Pan flat pattern regenerated for rev C bend allowance, the Side Rail DXF and the Weld Fixture locators moved for the rev C flange. This builds a multi-file change package, which is roughly Bananaz's redline report done by orchestration.
3. **Standards-grounded DFM checklist:** join `DesignChecklist` / `DesignChecklistResult` (`dfm_sheet_metal`, `dfm_machining`, …), `DesignStandard` (category `dfm`, `gdt`) and open critical `DesignFeedback`. The Battery Tray blocker is *"2.0 mm inside bend radius on 2.0 mm HR — cracking risk"*. This is a subset of AutoReview built from data that already exists.
4. **Pre-release blast radius:** call `release_readiness` + `release_impact.get` to tell the engineer what a release would touch. Where the two disagree, the agent should flag the inconsistency rather than trust either.
5. **Mid-task state change (the brief requires one):** write findings back as `DesignFeedback` (`ai_generated`), or create/bind a `DesignReview` to the exact version, which clears the `no_review_binding` blocker. Then advance it with `start_review` / `begin_work`. `DesignMarkup` can hold the structured finding.
6. **Honest refusal + escalation:** refuse geometric claims (no B-rep) and refuse release (`design_admin` only). Hand the session to a named human through `endpoint.agent_governance.escalations.raise` (`reason_code: policy_refusal`).

---

## 5. What Can Our Agent Do That Their Products Cannot?

Their products make a human reviewer fast. A person still opens the model, finds the revision, reads the checklist, chases the supplier and decides. Our agent can take *"what changed between rev B and rev C and is it manufacturable?"* as one goal and work it end to end, alone:

1. Work out which versions "rev B" and "rev C" actually are (the numbering differs per file).
2. Extract the delta and follow it into the flat patterns and weld fixture that depend on it.
3. Check the delta against DFM checklists, standards and the open critical finding (bend radius vs. material thickness).
4. Compute what a release would affect (items, work orders, demand) and notice when the platform's numbers do not add up.
5. **Re-read** readiness immediately before acting, because other agents change the book underneath it.
6. Record its findings as feedback, bind a review to the exact version, and either move it forward or refuse and escalate to a named `design_admin`.

It also does this across **two jurisdictions**, Suryodaya and Keystone. None of the benchmarked products operate across tenants like that or explain a refusal against a permission boundary.

---

## 6. Additional Findings From the 2026-09-24 Re-verification

**Candidate platform bugs (not yet filed; reproducible, read-only repro):**

| # | Area | Finding | Evidence |
| :-- | :-- | :-- | :-- |
| **C-1** | `endpoint.designreview.release_impact.get` | The blast radius attributes items to files they are not related to. The tenant has only **2** `DesignBOM` rows, owned by *BT-2400 Base Pan DXF* (`f82743f6…`) and *PMP-BRKT Mounting Bracket* (`725be709…`). Yet `release_impact.get` for the Battery Tray, Propeller, Trolling Propeller and Bench Vice all list "items on old version" pinned via those two BOMs, while reporting `counts.design_boms: 0`. The Battery Tray's own item (*Bharat EV — Battery Tray Assembly 2400*) is not listed for it. | `file_id` `39b69109…`, `1131649b…`, `25247d97…`, `4e6bf256…` |
| **C-2** | `DesignFile.released_version_id` | There is no referential integrity. `DF-2026-00100` has **0 versions**, yet its `released_version_id` (`5b05fe21…`, `released_at` 2025-10-15) is v2 of a *different* file (*BT-2400 Side Rail DXF*), while `release_readiness` reports `no_version`. **84/100** files have a `released_version_id` set while in a non-approved status. | `0bc2b05d-f11f-46b6-9b91-463af65eea8e` |
| **C-3** | `DesignFeedback` cross-links | Feedback `FB-2026-00030` is attached to file *AP-PR-002 Trolling Propeller* but to review *"Pump bracket rev B — tapping change review"*. Nothing checks that a feedback's review actually covers its file/version. | `b45f823e-4e88-4600-aca9-6660684988e8` |

**Gaps in our own submission (to close before grading):**

| Requirement (brief §8) | Current state | Action |
| :-- | :-- | :-- |
| ≥1 task whose correct answer is **refusal** | `tasks/t02_refusal_stub.json` is still a TODO stub | Hand-write it, e.g. "Release rev C to production now" (seat lacks `design_admin`; readiness `false`) or "Give me the minimum wall thickness of rev C" (no B-rep) |
| **Hand-written tests** (10 pts each) | `test_plan.md` exists; **0** test files in the repo | Team members write tests by hand; LLM-written tests score zero |
| Request includes a **state change in the middle** | Agent is read-only | Add a feedback/review write + transition, with a re-read before writing |
| Agent handles *the* request, not one file | `BATTERY_TRAY_FILE_ID` is hard-coded; one task only | Resolve the part by name/number; add the Propeller (rev B=v1, rev C=v2) and Bench Vice as tasks |
| Verifiers read the DB | Yes, but they substring-match the prose (`"2.0" in claim`) | Tighten to structured claims (JSON section) checked against DB values |
| Tolerate a changing platform | Synthesis prompt says `diff_from_parent_json` "is not populated", which is **no longer true**. The agent also still depends on tools that were removed (`compare`, `measure`, `DesignAICheck`) | Discover tools from `tools/list` at runtime; drop hard-coded platform assumptions |
| Both businesses | Only Suryodaya exercised | Run the task set on Keystone too |
| Model routing | `routing.yaml` quality tier leads with `claude-3-5-sonnet-20241022` (retired) and `gemini-2.5-flash`, not "Gemini 2.5 Pro" as earlier claimed | Update to current models |
| Transport hygiene | `as_client.py` uses `ssl._create_unverified_context()` | Use verified TLS |
| Step 2 "sign up for a trial" | Desk research only (no hands-on trial) | Book a CoLab / Bananaz / NexCAD trial or demo |
| "One page" | This report runs past one page | Condense §1–§5 into a one-page submission copy |

---

## 7. Filed Platform Bugs & Defect Log

All 23 reports filed by Team 21, from `GET /api/bug-report/mine` on both tenants, synced 2026-09-24 into [`myfiledbugs.json`](myfiledbugs.json). All are status `new`.

**Suryodaya (11)**

| Bug ID | Page | Title | Note |
| :--- | :--- | :--- | :--- |
| `fb28c793-d3a4-4eb3-b5eb-0ddab7bb1b49` | `DesignFile:4e6bf256…` | Feedback panel crashes: `TypeError: e.tags.split is not a function` | UI crash |
| `1b711d71-a408-4dc4-aff0-a26c30cc25cb` | `DesignReview:Home` | Design Review accepts a Due Date in the past | Validation |
| `03867387-0f63-4b60-ba3b-e399bc3f5bca` | `DesignVersion` | `diff_from_parent_json` / `file_hash` null | ⚠️ **Out of date:** populated as of 2026-09-24 (hash lineage only); follow up with a correction |
| `75676365-fd43-43b3-abfb-c20ac8359f53` | `DesignFile:analysis` | CAD analysis endpoints return 501 (no OpenCascade) | Still reproduces |
| `7bd75b49-8e0e-4da5-8609-16510b39a0bf` | `DesignFile:CADViewer3D` | 3D viewer: glTF route 404 | Still reproduces |
| `fba307c6-ba49-4ddc-9720-4d108c9ad97a` | `DesignReview:Home` | 3D viewer: glTF route 404 | Duplicate of `7bd75b49` |
| `9bda8688-db7d-40ac-b153-bab1ae877570` | `DesignAICheck` | `ai-review` returns 501 | Capability gap |
| `6949b3f7-a053-42d6-8e17-ee53668df04f` | `DesignComparison` | Comparison misses geometric/feature deltas | Still true |
| `0b60fa80-b075-4cc5-995b-e90fa09158f2` | `DesignFile:Artifacts` | No FAI / ballooning models | Feature request |
| `16497568-ed39-4e5b-8220-74237f0b0298` | `DesignReview` | No change-by-change approval states | Feature request; states quoted wrongly |
| `a9e1e306-d4e7-4f0d-9989-885f16cecbe5` | `DesignFeedback` | No cross-project precedent search | Feature request |

**Keystone (12)**

| Bug ID | Title | Note |
| :--- | :--- | :--- |
| `b4cc7be0-2c49-402d-888b-426978da0678` | Projects: Active card shows 0, Active page lists 5 (Archived card also 0) | UI count bug |
| `1485c79f-6c1c-4519-92b9-d5198530fa7e` | Files: Ready card shows 28, Ready page lists 14 | UI count bug |
| `f90310be-7da9-4b19-a9e0-91930b55ea1a` | Ready page: broken thumbnails for glTF/STL files | UI bug |
| `eb9657f9-7519-4958-944e-cfa4ba78973c` | Dashboard: "Critical" sorted last in Feedback by Priority | UI bug |
| `a5ec5529-647a-445d-86c5-22e08e6b8a18` | `diff_from_parent_json` / `file_hash` null | Same as `03867387` (also out of date) |
| `4e135c40-a844-4d64-970f-b22d13ab5716` | CAD analysis endpoints 501 | Same as `75676365` |
| `bc2ed2de-b8fd-45cd-9a1e-d14ffebebec1` | 3D viewer glTF 404 | Same as `7bd75b49` |
| `875397e2-023c-4dfa-b5ca-190800805003` | `ai-review` 501 | Same as `9bda8688` |
| `f18bae84-d343-4b62-a022-7a2f57c82c47` | Comparison misses geometric deltas | Same as `6949b3f7` |
| `28aed765-6107-4997-a617-e3dcfa43661c` | No FAI / ballooning models | Same as `0b60fa80` |
| `62e2bce9-34e2-4865-a026-69f2fe9cb30b` | No change-by-change approvals | Same as `16497568` |
| `e2e61540-3914-4b2f-86e9-4c57b497079c` | No cross-project precedent search | Same as `a9e1e306` |

> **Distinct defects:** 8 of the 12 Keystone reports re-file Suryodaya issues, and `fba307c6` duplicates `7bd75b49`. That leaves about **14 distinct issues**, of which 4 are feature requests rather than defects. The strongest *bug* candidates for the 100-point category are the UI crash, the date validation, the four Keystone UI count/sort/thumbnail bugs, the glTF 404, and C-1/C-2 above.

---

## 8. Sources & References

- **Bananaz AI:** [Design Agent](https://www.bananaz.ai/product/design-agent) · [Collaboration & Review](https://www.bananaz.ai/product/collaboration) · [Automated Release Artifacts: FAI, Redlines & Autoballooning](https://www.bananaz.ai/blog/automated-release-artifacts-fai-redline-ballooning) · [PDM as Single Source of Truth](https://www.bananaz.ai/blog/pdm-sync-single-source-of-truth) · [Pro vs Light Model](https://www.bananaz.ai/blog/ai-engineering-drawing-review-pro-model) · [2026 AI Stack for Mechanical Engineers](https://www.bananaz.ai/blog/ai-for-mechanical-engineers-2026-stack)
- **CoLab Software:** [Product Overview](https://www.colabsoftware.com/product/overview) · [AutoReview](https://www.colabsoftware.com/product/autoreview) · [AI CAD Review](https://www.colabsoftware.com/product/ai-cad-review) · [AI Transformation](https://www.colabsoftware.com/ai-transformation) · [Research: Importance of Design Standards](https://www.colabsoftware.com/research/importance-of-design-standards) · [Research: Who Participates in Design Reviews](https://www.colabsoftware.com/research/who-really-participates-in-engineering-design-reviews) · [Guide: AI Simulation Tools](https://www.colabsoftware.com/guides/ai-powered-simulation-tools-smarter-faster-design-validation) · [Digital Engineering 24/7 on AutoReview](https://www.digitalengineering247.com/article/colabs-adam-keating-on-autoreview-an-ai-powered-drawing-review)
- **OpenBOM & PLM:** [CAD File Agent & Product Memory (Aras ACE 2026)](https://www.openbom.com/blog/webinars-live-demos-conferences/openbom-at-aras-ace-2026-cad-file-agent-product-memory-and-ai-for-engineering) · [Connected BOM for AI Agents](https://www.openbom.com/blog/openbom-manifesto/connected-bom-product-data-foundation-ai-agents-engineering) · [Oracle PLM 26B Design-to-Source](https://blogs.oracle.com/scm/meet-the-agentic-ai-design-to-source-workspace-for-plm-from-cad-to-confident-sourcing-decisions)
- **NexCAD:** [nexcad.ai](https://nexcad.ai/)
- **CAD kernel:** [Open CASCADE Technology](https://www.opencascade.com/occt3d-technology/)
