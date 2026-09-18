# Design Review — Gap Report (team21)

Competitor researched: **CoLab Software** (colabsoftware.com), specifically
**AutoReview** (v2.0, part of the "CoLab 4.0" release, 2026) — an AI-native
design-review layer, not a twenty-year-old PDM tool with a chatbot bolted
on. Chosen because its feature set maps almost feature-for-feature onto our
own `designreview` entity model (`DesignReview`/`DesignFeedback` ↔ their
review/markup; `DesignComparison` ↔ their "Comparisons"; `DesignAICheck`
check types `dfm_analysis`/`gdt_check`/`lessons_learned` ↔ their AutoReview
+ AI Lessons Learned agent almost exactly), which makes the comparison
unusually direct. Researched via public product pages, press coverage of
the 4.0 launch, and trade-press interviews (see Sources) — not a hands-on
trial (no account signup completed yet).

## 1. What do they do that we do not?

- **AutoReview actually runs.** It's an AI peer-checker that contextually
  applies a company's DFM/GD&T/tolerancing/BOM-consistency rules to a CAD
  model and writes AI-generated markups directly into the review. Our
  equivalent surface — `DesignAICheck` (`check_type: dfm_analysis`,
  `gdt_check`) and the `ai-review` endpoint — exists in the schema and API
  but returns `501`: *"AI design review is not implemented in this build:
  it needs a model call that has not been wired up."*
- **An AI Lessons-Learned agent** that surfaces relevant feedback from past
  reviews automatically when a new part is reviewed. We have the exact
  concept modeled (`DesignAICheck.check_type = "lessons_learned"`) but
  nothing implements it — no automated surfacing exists.
- **Real geometry-aware DFM** — wall thickness, draft angle, GD&T — backed
  by an actual CAD-capable compute pipeline. We have the identical concept
  (`analysis/thickness`, `analysis/interference`) but both `501`: *"needs a
  CAD kernel (OpenCascade), which this build does not ship."* Verified
  platform-wide: zero files in the live Suryodaya instance have usable
  BREP geometry.
- **Live CAD/PLM integrations** (Creo, NX, SolidWorks, Windchill,
  Teamcenter, 3DEXPERIENCE) — reviews happen against authoring tools
  directly. We only ingest uploaded static files (STEP, etc.).

  **Contextual, cross-repository PDM/PLM precedent search** Competitor platforms (and AI-driven CAD tools) index historical PDM/PLM systems and past engineering review archives to answer questions like "Was a similar part manufactured before, and what issues occurred?". AgentSwitch's designreview seat lacks cross-project semantic search and historical issue indexing, meaning past review lessons remain isolated in static review silos.

  
- **A cross-review knowledge graph** organizing feedback so it's reusable
  across projects, not just attached to one review.

## 2. Which gaps can an agent close with tools our seat already has, and which need platform work?

**Platform gap (theirs to fix, not agent-orchestratable today):** no CAD
kernel means no real geometry diffing, wall-thickness, or interference
check is possible at all right now, regardless of agent cleverness — the
`compare` endpoint's own output says so explicitly
(`"geometry_compared": false` even on the platform's *only* file with
`has_brep: true`). And no wired model call means the platform itself
cannot produce a `DesignAICheck` — that's new infrastructure, not
orchestration.

**Orchestration an agent can do today, without new platform capability:**
- Because `DesignComparison`'s part-list-only fallback silently misses
  non-part-count changes (verified live: comparing a real rev 2→3 pair
  whose commit messages describe a hardened jaw insert, a thread-form
  change, and added ribbing returned `0 added/removed/modified` — the diff
  tool alone would report "nothing changed"), the agent must additionally
  read `DesignVersion.commit_message`/`metadata_json` across the version
  chain rather than trust the comparison record in isolation.
- A lightweight "lessons learned" layer is buildable purely from
  `DesignFeedback.list` (filtered to `ai_finding`/`issue`),
  `DesignStandard.list` (`category: dfm`), and `DesignChecklistResult.list`
  — reading history across projects to inform judgment on a new part,
  the same shape as CoLab's agent, just running as our agent's own
  reasoning instead of a platform feature.
- `endpoint.designreview.release_readiness` is real and works today — it's
  the one manufacturability-adjacent signal actually implemented
  (conversion status, review binding, open blockers) and should anchor the
  agent's manufacturability answer rather than the stubbed analysis
  endpoints.

## 3. What can our agent do that their product cannot?

CoLab's AutoReview flags issues; a human still reads the markups, decides
the review outcome, and clicks through create/accept/reject/complete on
each piece of feedback — it is a UI a person drives one step at a time.
Our agent can hold "what changed between rev B and rev C, and is it
manufacturable" as one continuous goal across the whole tool chain: call
`compare`, notice `geometry_compared: false`, fall back to reading
`commit_message` text itself, cross-reference `release_readiness`
blockers, form a manufacturability judgment, and walk the
`DesignReview.start_review → DesignFeedback.create → DesignReview.complete_review`
state sequence end to end — without a human driving each screen.

It can also do something CoLab's own AutoReview history argues *for*
explicitly: refuse rather than fabricate. Our platform's `ai-review`
endpoint used to silently create "a `DesignAICheck` marked 'completed'
with 0 findings and `model_used='placeholder'` — a finished-looking review
that never ran" before it was changed to return `501` instead. That is
exactly the failure mode our agent must avoid — when there's no BREP
geometry to inspect, it should say the geometry-based DFM claim can't be
made, not present a confident-looking verdict anyway.

---
Sources:
- [AutoReview: AI Design Reviews That Catch Issues Earlier | CoLab](https://www.colabsoftware.com/product/autoreview)
- [AI CAD Review for DFM Checks | CoLab](https://www.colabsoftware.com/product/ai-cad-review)
- [CoLab Software homepage](https://www.colabsoftware.com/)
- [CoLab Launches 4.0 — Built for Human Decision-Makers in an AI-First World (BusinessWire)](https://www.businesswire.com/news/home/20260709486911/en/CoLab-Launches-4.0-Built-for-Human-Decision-Makers-in-an-AI-First-World)
- [CoLab 4.0 adds AI tools for engineering reviews (Engineering.com)](https://www.engineering.com/colab-4-0-adds-ai-tools-for-engineering-reviews/)
- [CoLab launches AutoReview AI tool for design review (Engineering.com)](https://www.engineering.com/colab-launches-autoreview-ai-tool-for-design-review/)
- [CoLab's Adam Keating on AutoReview, an AI-Powered Drawing Review (Digital Engineering 24/7)](https://www.digitalengineering247.com/article/colabs-adam-keating-on-autoreview-an-ai-powered-drawing-review)
- Platform findings: live testing against `agentswitch.theschoolofai.in` API on 2026-09-16, detailed in `PLAN.md` §8.

**Caveat**: this is desk research (product pages, press coverage), not a
completed trial/demo — CoLab's own materials are inconsistent on whether a
public API exists (one product page lists "API integration," a separate
summary states no public API), which a hands-on trial would resolve. Worth
30–60 minutes signing up for a CoLab trial before this is finalized, per
the brief's "a day here is not too much."
