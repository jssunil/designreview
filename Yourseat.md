Your seat
Seat 21 · Design Agent

Design Review

What changed between revisions, and whether it can be made.

The questions it must answer

Any DFM problems here?

design.dfm_problems

What changed between rev B and rev C?

design.rev_b_to_rev_c

A tick means a predicate already checks that answer against the database. An empty circle means nothing verifies it yet — that is the work.

What already exists
The data. One shared database, the same for every team. All 35 seats and 63 goals are already defined.
The API. Full CRUD over every entity, plus the module endpoints. Nothing needs to be built to read or write business data.
The runtime. Sessions, jobs, approvals, tool policies and the audit trail. Your agent runs inside it rather than beside it.
The evaluator. 15 of 63 goals have a predicate that decides, from the database alone, whether the answer was right.
What you build
One agent, for your seat, that answers its questions using this API. You run it on your own machine with your own model keys. Answers are judged by the seat's predicate against real data — not by how the reply reads, so an agent that sounds right and gets the number wrong fails.

What you may change
Yours: your agent's code, prompts, tools, planning, retries and model choice.
Ask first: the harness itself — the runtime, the predicates, the schemas. If your seat genuinely cannot be done without a change there, report it as a bug with the case that proves it, and it gets fixed for everyone rather than forked.
Never: another team's agent, or the shared data as a way of making your own goal pass. Every write is attributed in the audit trail.
When something is broken
Use the bug button in the header, on the screen where it happened. Say what you did, what you expected, and what happened instead — a report that cannot be reproduced cannot be fixed.

Reference
API explorerEvery endpoint, grouped by app. Try calls in the browser.
API referenceThe same surface, laid out for reading rather than poking.
Entity schemasFields, types, options and required lists for all entities.
Agent toolsThe tools an agent may call, each with its JSON Schema.