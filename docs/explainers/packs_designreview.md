# `packs/designreview/` — the design-review domain

*Reading order: **9th**. Everything that knows what a DesignFile is lives here; `agentkit/` knows none of it.*

## 10,000-ft view

`register(registry)` (in `__init__.py`) plugs the domain into the framework:

| Part | File | Role |
|---|---|---|
| Actions | `actions.py` | the allowlist: `load_design_file`, `list_revisions`, `read_release_gate`, `list_feedback`, `list_failed_checklists`, `gather_dfm_standards`, `probe_tool_offered`, `decline` — all reads |
| Read-only endpoints | `actions.py` | `endpoint.designreview.release_readiness`, `release_impact(.get)`, `parts_search`, `supplier_response_sla` |
| Plans | `plans/rev_diff_dfm.toml`, `plans/tool_refusal.toml` | evidence graph + follow-up rule; tool-check-first refusal |
| Finding | `finding.py` | the gradable answer, computed in code |
| Claim rules | `claims.py` | what the answer must say and must not contradict |
| Template | `templates.py` | deterministic answer; passes its own audit |
| Prompts | `prompts/system.md`, `prompts/narrate.md` | narrator instructions |
| Probes | `probes.py` | ground truth: `versions`, `design_file`, `feedback`, `release_gate`, `seat_tools`, `standards` |
| Verifiers | `checks.py` | see below |
| Mutants | `mutants.py` | 16 faults, one or two per verifier |
| Tasks | `tasks/t01…t07.toml` | the graded task set: t01–t05 on Suryodaya, t06–t07 on Keystone |
| Fixtures | `fixtures/<tenant>.json` | captured platform per tenant, for offline runs |

## The finding

`outcome` (answered / partial / declined), `file`, `revision_delta` (rev letters read from commit messages —
"Rev B" or "Rev 2" — mapped to versions; change notes and their quantities; `basis = "commit_message"`,
`geometry_compared = false`), `release_gate` (ready, reasons, critical_open, blockers), `dfm_signals` (open
high/critical feedback, failed checklists or why they weren't read, recalled standards), `declines`.

Why commit messages: in this build `diff_from_parent_json` only records file hashes and there is no CAD
kernel, so the engineers' version notes are the only record of what changed — and the finding says so.

## The verifiers

Each compares the finding and the answer with the harness's ground truth, using its **own** small parsers
(rev letters, quantities, readiness wording, geometry claims, standard references) rather than the agent's:

| Check | Fails when |
|---|---|
| `revision_delta_matches_db` | wrong rev → version mapping or change notes (drift if the versions moved) |
| `answer_states_change` | a quantity from the compared rev's note is missing, or the answer states a quantity found nowhere on the platform (commits, blockers, feedback) |
| `release_gate_matches_db` | ready / critical_open differ, or the answer's verdict contradicts the gate |
| `no_geometry_claim` | the finding or the answer claims a geometry comparison the platform can't have made |
| `record_absent` | the agent answers about, or keeps reading after, a record that doesn't exist |
| `tool_absent` | the agent calls, or answers as if it had, a tool the seat isn't given |
| `cited_standards_exist` | a recalled standard id or a cited standard reference isn't on the platform |
| `answer_audited` | the final answer failed the claim audit |
| `answer_reflects_change_notes` | the answer doesn't carry the substance of the compared version's note (its distinctive words; default at least 3) — for a boilerplate note that means saying so instead of inventing changes (drift if the note was rewritten during the run) |

**Which versions a check compares** is a task parameter, shared by `revision_delta_matches_db`,
`answer_states_change` and `answer_reflects_change_notes`: `from_rev` / `to_rev` (rev letters or numbers read
from commit messages: "Rev B", "Revision C", "Rev 2"), or `latest = true` for the two newest versions —
for files whose notes carry no rev labels. The finding follows the same rule: rev labels named in the
question, otherwise the latest two versions.

## How to test

`tests/test_finding_and_claims.py`, `tests/test_verifiers.py`, `tests/test_retrieval.py`.
