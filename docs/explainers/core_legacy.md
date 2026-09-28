# `core/` — legacy import paths

*Nothing new should import from `core/`. It exists so older scripts and notes keep working.*

| Module | Status |
|---|---|
| `core/harness.py` | re-exports `TaskRun`, `Step`, `Harness` from `agentkit/record/run_record.py` (same API) |
| `core/llm_gateway.py` | re-exports `LLMGateway`, `LLMReply`, `ProviderFault`, … from `agentkit/llm/gateway.py`; `python core/llm_gateway.py` still smoke-tests the gateway |
| `core/economics.py` | re-exports `CostLedger` & co. from `agentkit/llm/economics.py` |
| `core/routing.yaml` | stub; routing lives in `config/routing.toml` |
| `core/dag_engine.py` | the first draft's graph engine; **not used by the agent** (see `agentkit/graph`). Kept until its remaining tests are retired. Needs `networkx`. |
| `core/eval_framework.py` | the first draft's axis scorer (`evaluate_run`); not used by the harness |
| `core/judge.py` | single-pass LLM-as-judge rubric; not part of grading, usable as an optional extra signal |

When these are removed, drop `networkx` from `requirements.txt`.
