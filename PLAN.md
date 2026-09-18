bugs
# Design Review Seat — Understanding & Plan (team21)

Source docs: `introduction.txt`, `agentswitch.md` (team brief, read 2026-09-16).

## 0. Action plan — what to do next for the week-one deliverable

The week-one deliverable is the **gap report only** (Section 8, step 3 —
one page, three questions). Status:

1. ~~Step 2 research~~ **Done (desk research)** — see `GAP_REPORT.md`.
   Competitor chosen: **CoLab Software / AutoReview** (AI-native, 2026
   "CoLab 4.0" release), picked because its feature set maps almost
   1:1 onto our `designreview` entity model. Based on product pages and
   press coverage, **not** a completed hands-on trial — see the caveat at
   the bottom of `GAP_REPORT.md`. Worth 30–60 min signing up for a real
   trial before this is finalized.
2. ~~10-minute UI walkthrough~~ **Done** (browser session was already
   authenticated as team21 — no credential entry needed). Findings folded
   into §10 below, including a real, reproducible frontend crash found
   while looking at the Bench Vice Assembly's feedback panel.
3. ~~Draft the one-page gap report~~ **Done** — `GAP_REPORT.md`, built from
   §8's live findings (CAD-kernel/AI-review `501`s, the part-list-only
   diff fallback, `release_readiness` as the one working manufacturability
   signal) plus the CoLab research.
4. **Still open — user review before submission.** The brief frames this
   as *our* judgment call on a competitor and *our* argument for what's
   orchestration vs. platform gap; worth a human pass over
   `GAP_REPORT.md` — and ideally the CoLab trial from #1 — before it's
   turned in, not just an agent draft.

Not needed for week one (Step 4 — agent + harness — comes later): left in
§6/§7 for later reference, not blocking this deliverable.

## 1. What this project actually is

AgentSwitch is a live, shared business platform (424 entity types) with two
real company instances — Suryodaya Precision Works (India, GST/Ind AS) and
Keystone Precision Works LLC (US, Sales & Use Tax/ASC). Every team is
assigned one **seat** (an app + role set) and must build an autonomous agent,
driven over MCP, that can answer one hard, multi-step, judgement-requiring
question against that live data. This is not a CRUD demo — the bar is an
agent that holds a goal across many steps, re-reads state that other agents
are concurrently changing, and knows when to refuse.

## 2. Our seat

- **Team:** 21
- **Seat:** Design review (`designreview`)
- **App scope:** design files, versions, reviews
- **Also available to us:** `agent` (our own private agent workspace) and
  `crm` (shared customer spine)
- **Cannot see:** any other team's app data (403 by design — not a bug)
- **Shared book caveat:** N/A in the same way as Ledger (01/02/03) — need to
  confirm during Step 1 whether any other team shares the `designreview` book
  on either instance.

### The request we must ultimately answer

> "What changed between rev B and rev C, and are there manufacturability
> problems in this part?"

This has two distinct sub-questions the agent must handle in one pass:
1. **Diff** — compare two design revisions (rev B vs rev C) of a part and
   summarize concrete changes (geometry, dimensions, tolerances, material,
   notes, BOM references, whatever the schema actually models).
2. **DFM judgement** — assess the *current* revision for manufacturability
   problems (tolerances too tight for process, thin walls, undercuts,
   missing GD&T, material/process mismatch, etc.), which requires either
   platform data (process capability rules, prior review comments) or
   domain reasoning layered on top of the schema.

## 3. Credentials & access

Both logins live in `agentswitch.md` (not duplicated here). Two businesses,
one password each, same email `team21@theschoolofai.in`. Three doors onto
the same data/rules: MCP (`POST /api/mcp`, JSON-RPC 2.0, the interface to
build against), the web UI (for human understanding), REST (fallback,
documented at `/docs`, `/redoc`, `/api/schemas`).

Key operational facts to remember while building:
- JSON-RPC errors return HTTP 200 — only auth failures are HTTP-level (401).
  Must check the envelope, not just the status code.
- Tool catalogue is pre-scoped to our seat; a tool for another app simply
  isn't in `tools/list` — nothing to request access to, nothing to debug.
- Argument schemas are closed — unknown args are rejected, not ignored.
- Data can change under us between reads (other agents/teams writing
  concurrently) — the agent must re-read before acting, never assume
  staleness is safe, and never "tidy up" data it doesn't own.

## 4. The four-step process (Section 8) and where we are

| Step | What it is | Status |
|---|---|---|
| 1. Learn the domain | UI walkthrough (~10 min), read `/api/schemas` for our entities, learn workflow states/transitions | **Not started** |
| 2. Find the best product in our domain | The step teams skip. Must independently research the leading modern (ideally AI-native) product doing design review / revision management / DFM in the wild | **Not started** |
| 3. Write the gap report | 1 page, 3 questions, due week one (see §5) | **Not started** |
| 4. Build the agent, then the harness | Agent answers the seat's question; harness proves it with disk-logged runs and DB-reading verifiers, not agent prose | **Not started** |

## 5. Step 3 — Gap report shape (from the Rillet worked example)

Three questions to answer with *specifics*, not vague impressions:

1. **What do they do that we do not?** Concrete named features (e.g. "no
   automated DFM check on upload," "no diff view between CAD revisions,"
   "no reviewer sign-off workflow with e-signature") — modeled on the
   Rillet-vs-Ledger example's specificity bar.
2. **Which of those gaps can an agent close with tools our seat already
   has?** Split into: (a) orchestration we can build now over existing
   `DesignFile`/`DesignVersion`/`Review`-type entities, vs (b) gaps that
   need new platform tables/endpoints (their problem, not ours).
3. **What can our agent do that their product cannot?** The product is a UI
   a human drives one click at a time. Our agent can hold "what changed
   between rev B and rev C, and is it manufacturable" as one continuous
   goal — pull the diff, cross-reference tolerances against process
   capability, check review history, and produce a judgement in one pass
   without a human clicking through screens.

### Candidate products to evaluate in Step 2 (to verify/narrow via real research, not assumed)

Design-review / PLM / revision-control space, prioritizing AI-native or
API-first products (per the brief's instruction to favor products built in
the last ~3 years around an agent driving them):
- **Onshape** — cloud-native CAD with full branch/merge version history and
  a real REST API; good comparison for the "what changed between revisions"
  half.
- **Fictiv DFM Analyzer / Xometry Instant Quoting / Protolabs Hubs** —
  automated, instant manufacturability feedback on uploaded CAD; strong
  comparison for the "manufacturability problems" half.
- **Arena PLM / OpenBOM / Duro** — PLM-native revision and change-order
  management, useful for what a "real" design review workflow models
  (ECOs, approvals, BOM linkage).
- Look for something combining *both* halves (revision diff + DFM) in one
  AI-native product — that would be the single strongest comparator,
  analogous to how Rillet combines ledger + AI accruals. Worth a real
  search before committing to one.

This list is a starting point for Step 2's research, not a substitute for
it — the brief is explicit that finding the product is our work.

## 6. Step 1 — verified against the live Suryodaya instance (2026-09-16)

Logged in as team21 (`role: design_user`, `allowed_apps: ["designreview","agent","crm"]`)
and pulled `/openapi.json`, `/api/schemas`, and a real MCP `tools/list`
(322 tools scoped to this seat). This replaces the guesswork below with
grounded facts.

### Entity model (24 `designreview`-domain entities in `/api/schemas`)

Core objects for the request: `DesignProject` → `DesignFile` (a CAD
part/assembly/drawing) → `DesignVersion` (one revision of a file, with
`version_number`, `parent_version_id`, `diff_from_parent_json`,
`commit_message`, `file_hash`). Also: `DesignAssembly` /
`DesignAssemblyVersion` (assembly-level, with its own `diff_json`),
`DesignComparison` (a persisted diff between two versions —
`comparison_type: geometry_diff|drawing_overlay|assembly_diff|side_by_side|part_list_diff`,
`added_count`/`removed_count`/`modified_count`, `diff_result_json`,
`diff_gltf_file`), `DesignReview` (review workflow, `review_type` includes
`dfm_review`), `DesignFeedback` (issues/findings including
`ai_generated`/`ai_finding` items, with `standard_reference` and cost-impact
fields), `DesignChecklist`/`DesignChecklistResult` (categories include
`dfm_machining`, `dfm_molding`, `dfm_sheet_metal`, `dfm_casting`, pass/fail
results), `DesignAICheck` (a run of AI analysis; `check_type` includes
`dfm_analysis`, `gdt_check`; has `findings_count`, `critical_count`,
`model_used`, `summary`), `DesignStandard` (reference standards, category
includes `dfm`, `gdt`, `tolerancing`), `DesignFile.status` running through
flow `DesignFileFlow` (transitions seen in MCP: `process`, `complete`,
`fail`, `retry_conversion`, `approve_release`, `reopen_for_design`).

**This means the platform already models both halves of our request as
first-class data** — it is not something we have to invent from scratch:
revision diffing is `DesignComparison`/`DesignVersion.diff_from_parent_json`,
and manufacturability is `DesignAICheck`(dfm_analysis) +
`DesignChecklistResult`(dfm_* categories) + `DesignFeedback`(ai findings) +
`DesignStandard`(dfm rules).

### Endpoints that exist in REST but NOT as MCP tools (important gap)

`/openapi.json` lists 41 `designreview`-specific REST paths, including real
compute: `analysis/thickness` (wall-thickness via ray casting — classic DFM
check), `analysis/interference` (assembly clash detection),
`analysis/mass-properties`, `ai-review` (triggers a Claude-based review that
writes `DesignFeedback`), `compare-drawings` (pixel diff on 2D drawings),
`merge`, `simulation/analyze`.

**Only a subset of these are exposed as MCP tools** for our seat:
`endpoint.designreview.compare` (geometry diff between two version IDs —
directly answers "what changed between rev B and rev C"),
`endpoint.designreview.measure` (server-side BREP distance/angle/radius —
usable as a manual DFM probe), `endpoint.designreview.release_readiness`
(reads aggregated release-readiness evidence for a file — likely the
single best manufacturability rollup available to us), `endpoint.designreview.upload`,
`endpoint.designreview.parts_search`, `endpoint.designreview.supplier_response_sla`.
`analysis/thickness`, `analysis/interference`, `ai-review`,
`compare-drawings`, `merge`, and `simulation/analyze` are **absent from
`tools/list`** — per §6 of the brief, an absent tool is scoping, not a bug,
so the agent cannot trigger them over MCP. Also, `DesignAICheck` only has
`list`/`get` in MCP (no `create`) — the agent can read past AI-check
results but cannot request a new one through MCP.

**Design decision for Step 4**: either (a) build the manufacturability
answer entirely from what MCP exposes — `release_readiness`, `measure`,
existing `DesignAICheck`/`DesignChecklistResult`/`DesignFeedback` rows,
`DesignStandard` rules — or (b) fall back to REST (permitted per §7, "the
fallback") to call `analysis/thickness`/`analysis/interference`/`ai-review`
directly when no prior check exists for the version in question. Worth
deciding deliberately rather than defaulting to REST for everything, since
the brief is explicit that MCP is the interface to build against.

### Remaining Step 1 actions

1. Open the Suryodaya UI, find a real `DesignFile` with ≥2 `DesignVersion`
   rows, and look at an actual `DesignComparison`/`DesignAICheck` record if
   one exists, to see real `diff_result_json` / `summary` shapes rather
   than just field names.
2. Check whether `designreview` is a shared book the way Ledger is (call
   `DesignFile.list`/`DesignProject.list` and see if data already exists
   from a prior run or another team on this seat type — no other team
   currently owns `designreview`, per the seat table in §2, so this is
   likely *not* shared, unlike Ledger's 01/02/03).

## 7. Step 4 — what "harness" means here (per grading criteria)

Not just an agent that answers well once. Need:
- Our own loop/harness (not a wrapper around AgentSwitch's own tooling).
- A task set with **verifiers that read the database directly**, not ones
  that trust the agent's own prose output.
- Every run logged to disk before scoring.
- At least one task in the set whose correct answer is **refusal** (ask for
  something the data can't support or the seat isn't permitted to do) — an
  agent that fabricates a confident answer fails that task regardless of
  how well it does elsewhere.
- Hand-written tests only (10 pts/test); a test authored by an LLM scores
  zero — so test-writing is explicitly on us, not delegable to an agent.
- 100 points per real AgentSwitch bug found (via in-app "Report a problem"
  or `POST /api/bug-report`) — worth watching for during Steps 1 and 4, but
  known non-bugs (403 on other apps' data, concurrent shared-book edits,
  scoped-away tools, Keystone's placeholder companies/hidden AgentTask
  rows) are explicitly not reportable.

## 8. Live findings — real `diff_result_json` and DFM content (2026-09-16)

Ran the actual tools against live Suryodaya data instead of reading schemas
in the abstract. This changes several assumptions in §6.

### A real rev-B-vs-rev-C example already exists

`DesignFile` `ST-VICE-150 Bench Vice Assembly` (`4e6bf256-9e16-4158-bdf5-633b4bc2ecb4`)
has three real `DesignVersion` rows with genuine, coherent commit messages —
rare in this seed data (see below):

| version | id | commit_message |
|---|---|---|
| 1 | `d2cbcd88-c8b2…` | "Vice assembly rev 1." |
| 2 | `bbf5e3a2-03f7…` | "Rev 2 — jaw insert hardened, screw thread changed to trapezoidal." |
| 3 | `02beafcf-f354…` | "Rev 3 — body ribbing added, 180 g weight saving." |

Calling `endpoint.designreview.compare` (MCP) with
`source_version_id=bbf5e3a2…`, `target_version_id=02beafcf…`,
`comparison_type=geometry_diff` both computed **and persisted** a
`DesignComparison` row (`id: ced0737f-0a42-4b22-97af-2f5d64399bec`). Its
actual `diff_result_json`:

```json
{
  "added": 0, "removed": 0, "modified": 0, "unchanged": 11,
  "added_parts": [], "removed_parts": [], "modified_parts": [],
  "basis": "assembly part list (part number, name, quantity, material)",
  "geometry_compared": false
}
```

**Critical implication**: `geometry_compared: false` — the endpoint silently
fell back to diffing the assembly *part list* (count/name/qty/material)
rather than real geometry, and found nothing, because the real engineering
changes described in the commit messages (hardened jaw insert, thread-form
change, added ribbing, weight reduction) are property/geometry-level
changes, not added/removed/renamed parts. **A part-list diff cannot see
them.** An agent answering "what changed between rev B and rev C" from
`DesignComparison` alone here would wrongly report "nothing changed." The
commit_message field is currently the only reliable signal for this
example — the agent must read it, not just the diff.

### Three analysis endpoints are explicit stubs in this build (not just missing data)

Tested directly (REST, since none of these three are in MCP's `tools/list`
— see §6):

| Endpoint | Result |
|---|---|
| `POST /api/designreview/analysis/thickness` | `501` — *"Wall-thickness analysis is not implemented in this build: it needs a CAD kernel (OpenCascade), which this build does not ship."* |
| `POST /api/designreview/analysis/interference` | `501` — same reason, clash detection. |
| `POST /api/designreview/ai-review` | `501` — *"AI design review is not implemented in this build: it needs a model call that has not been wired up. It used to create a DesignAICheck marked 'completed' with 0 findings and model_used='placeholder' — a finished-looking review that never ran."* |
| `endpoint.designreview.measure` (MCP, IS listed) | Returns HTTP 200 (JSON-RPC envelope), but `result.success: false`, `status: "not_implemented"` — *"Server-side BREP measurement is not implemented in this build: it needs a CAD kernel (OpenCascade), which is not installed."* |

Confirmed platform-wide, not just for this file: only **one** `DesignFile`
in the entire Suryodaya instance has `has_brep: true`, and it's the same
Bench Vice Assembly — and even it shows `conversion_status: "pending"`.
There is no file anywhere with working BREP geometry. So `geometry_diff`
will never be true and thickness/interference/measure will never succeed,
on any part, in this build — it's an environment limitation, not a
per-file data gap. **This is unprompted, deliberate self-documentation from
the platform** (the 501 bodies read like intentional TODOs), which is
extremely useful: it tells us exactly what "AI review" and geometric DFM
checks currently *cannot* do, rather than leaving us to infer it from
silence.

`endpoint.designreview.release_readiness` **does work** and returns real
structured evidence — this is the one manufacturability-adjacent endpoint
that's actually implemented:
```json
{
  "ok": true, "available": true, "ready": false,
  "reason_codes": ["not_ready", "conversion_incomplete", "no_review_binding"],
  "reasons": [
    "The file is not in the canonical ready state.",
    "The selected version has no completed recorded conversion.",
    "No review is bound to this exact file version."
  ],
  "critical_open": 0, "blockers": [], "blockers_truncated": false
}
```

### Seed data is structurally rich but semantically noise (except the one file above)

100 seeded rows each for `DesignFile`, `DesignProject`, `DesignChecklist`,
`DesignStandard`, `DesignFeedback` — but names/descriptions are
randomly-shuffled text fragments, not coherent content (e.g. a
`DesignChecklist` named "Scriber 152mm (Nos)", `category: dfm_molding`,
described with an unrelated sentence about "Punch Set: lot 225 at
Kolhapur"; a `DesignStandard` named "Lathe Dog 346mm (Set)"). Zero
`DesignComparison` and zero `DesignAICheck` rows existed before we created
one just now. No `DesignVersion` row anywhere had `parent_version_id` or
`diff_from_parent_json` pre-populated — these are computed on demand, never
seeded.

**Consequence for the agent/harness**: the Bench Vice Assembly is
currently the *only* part on the platform with a genuine, legible revision
story. Building and testing the agent (and writing verifier tests) should
anchor on this file, and probably on 1–2 more we seed ourselves with
realistic `commit_message`/`metadata_json` content, rather than trusting
random seed rows to carry meaning.

### What this means for the gap report (updates §5)

- **Platform gap (their problem), not ours to orchestrate around**: no CAD
  kernel in this build → no real geometry diffing, no wall-thickness check,
  no interference/clash check, no server-side measurement. And no wired-up
  model call → no working AI review, so `DesignAICheck`/`dfm_analysis`
  can't be produced by the platform today.
- **What an agent can still do today (ours to build)**: manufacturability
  judgement has to come from *reasoning over existing structured/text
  signals* rather than triggering geometry compute — `commit_message` and
  `metadata_json` on `DesignVersion`, `manufacturing_process`/`material`
  on `DesignProject`, `release_readiness`'s real blocker evidence, and any
  `DesignChecklistResult`/`DesignStandard` rows that are actually
  meaningful (mostly ones we author) — combined with the agent's own
  domain knowledge (e.g. reasoning that "trapezoidal thread + hardened
  insert" changes machining process/tolerance risk). This is a stronger,
  more specific "what can an agent do that their product cannot" case than
  the generic Rillet framing: even *within a build that's missing its CAD
  kernel*, an LLM-driven agent can produce a defensible manufacturability
  read by triangulating text + metadata + workflow state, where a
  geometry-only DFM tool would simply have nothing to say.

## 9. UI walkthrough findings (2026-09-16)

Done via browser automation, already authenticated as team21 (session was
live in the browser — no credential entry required). Confirms the API-only
picture from §8 and adds one concrete bug.

- **Dashboard/nav matches the seat boundary**: left nav shows only
  `DESIGN REVIEW` under Operations plus `AGENT`/`MISSION CONTROL`/
  `JOB LEDGER`/`AGENT SKILLS` under Platform — no other team's apps, as §3
  of the brief describes.
- **Design Review dashboard** shows real aggregate counts matching the API:
  100 projects, 21 open reviews, 20 open feedback, 45 open design reviews,
  100 files (54 processing / 13 errors / 15 ready).
- **Opened `ST-VICE-150 Bench Vice Assembly` directly** (search found it by
  name, 1 result of 100 files). Confirms §8 exactly: 3D viewer panel is
  blank (no renderable geometry — `conversion: Pending`), file details
  panel shows `Part Count: 11`, `Has PMI: Yes`, `Status: Uploaded`. No
  version-history UI is exposed on this screen — version data is
  API/schema-only right now, not surfaced to a human reviewer in the UI.
- **Real bug found & filed**: clicking the file's **Feedback** button (badge said
  "8") crashes the screen — *"This screen hit an error… `e.tags.split is
  not a function`"*. Reproduced twice; console shows a real stack trace:
  `TypeError: e.tags.split is not a function` inside the feedback-rendering
  component (`DS` in `index-*.js`), thrown while rendering
  `DesignFile:4e6bf256-9e16-4158-bdf5-633b4bc2ecb4`'s feedback list. Root cause:
  `DesignFeedback.tags` is passed into `.split(',')` without a type guard.
  **Filed on 2026-09-17 via API** (Report ID: `fb28c793-d3a4-4eb3-b5eb-0ddab7bb1b49`, status: `new`).
  Repro: seat `design_user`/team21, Suryodaya, open `DesignFile`
  `4e6bf256-9e16-4158-bdf5-633b4bc2ecb4` (ST-VICE-150 Bench Vice Assembly) →
  click "Feedback (8)" → screen crashes.
- **Opened a real `DesignReview`** (`DR-2026-00091`, "SG iron casting
  shortage on the bench vice line", `ai_review` type, Draft). Renders
  cleanly: company/project/title/description/type/due-date/priority/
  checklist all shown with resolved links. Two UX gaps, not necessarily
  bugs: the "Files under review" and "Reviewers" child tables show raw
  UUIDs instead of resolved names, unlike every other reference field on
  the page (which do resolve to friendly links) — inconsistent with the
  rest of the UI's `_display` pattern seen everywhere else. Also
  `Resolved Feedback: 246513.22` — a non-integer value where a count is
  expected, another sign this row's seed data is nonsensical rather than a
  functional problem.

## 10. Open questions

Resolved via live API access (§6): entity model, MCP-vs-REST tool gap, and
that no other team's seat type is `designreview` (so the book is very
likely ours alone, unlike Ledger). Still open:

- Whether any `DesignFile`/`DesignProject` rows already exist (seeded) on
  Suryodaya/Keystone, or whether we start from an empty book and need to
  upload a real CAD file ourselves to get two revisions to diff.
- Real shape of `diff_result_json` / `DesignAICheck.summary` once a
  genuine comparison/AI-check exists — schema gives field names and types,
  not the actual content structure an agent would need to parse.
- Whether to lean on MCP-only tools (`release_readiness`, `measure`,
  reading existing `DesignAICheck`/`DesignChecklistResult`) or also call
  REST directly for `analysis/thickness`/`analysis/interference`/`ai-review`
  when no prior check exists — a build decision, not just a research one.
