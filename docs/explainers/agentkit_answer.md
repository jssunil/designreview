# `agentkit/answer/` — words checked against facts

*Reading order: **4th**. Files: `claim_audit.py`, `narrator.py`. The design-review rules live in
`packs/designreview/claims.py` and `templates.py`.*

## 10,000-ft view

The finding (the gradable answer) is computed in code before any LLM is involved. The LLM's only job is to
put it into words, and those words are audited against the finding. Unchecked LLM text is never the answer.

## Why it's written this way

- **Deterministic audit.** `audit_narrative(text, finding, rules)` reports:
  `unsupported_ids` (a UUID not in the finding), `unsupported_quantities` (a number-with-unit whose value is
  nowhere in the finding — numbers inside finding strings count, except the digits of dates, clock times and UUIDs), `contradictions` (pack-defined) and
  `missing` (required facts left out). No LLM grades another LLM here.
- **The pack decides the rules.** `ClaimRules(quantity_re, required, contradictions)` is supplied by the pack;
  this module holds no domain words.
- **Tell the model up front.** The first prompt already lists the facts it must state, so most first drafts
  pass; a failing draft gets exactly one rewrite, with the specific problems listed (`correction_note`).
- **Always an answer.** If the rewrite still fails, the LLM is unreachable, or the reply was cut off
  (`stop_reason = max_tokens`), the pack's deterministic template is the answer. The run records every attempt,
  its audit, and `source = "llm" | "template"`.

## The design-review rules (`packs/designreview/claims.py`)

- Quantities: numbers with mm, °, kg, g or % must appear in the finding.
- Required: the release verdict (ready / NOT ready), the open-critical count, every quantity in the notes
  being compared, and that the delta comes from commit messages, not a geometry comparison; for a refusal,
  that it can't be answered and why.
- Contradictions, sentence by sentence with negation handled: "ready for release" against a not-ready gate
  (and vice versa); claiming a geometry comparison was run; citing a standard (ISO/ASME/DIN/EN/…) that isn't in
  the finding. Standard prefixes are matched case-sensitively so "rev B is v2" isn't read as "IS v2".

## Key functions

| Function | Purpose |
|---|---|
| `audit_narrative(text, finding, rules) -> dict` | `ok` + the four problem lists |
| `correction_note(report) -> str` | the rewrite instruction |
| `compose_answer(query, finding, gateway=, rules=, template=, system_prompt=, instructions=)` | the narrate loop |
| `known_numbers(obj)` | every number anywhere in the finding, ignoring dates, times and UUIDs (a 12:05 snapshot must not support "12 mm") |

## How to test

`tests/test_finding_and_claims.py` — build a finding from hand-made `NodeResult`s; use a fake gateway object
with a `call()` that returns scripted strings.
