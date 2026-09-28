# `config/` and `agentkit/config.py` — configuration

*Reading order: **0th** (skim). All config is TOML (read by the standard library's `tomllib`) or JSON,
validated by pydantic models.*

| File | Model | Holds |
|---|---|---|
| `config/tenants.toml` | `TenantsConfig` / `TenantSpec` | per tenant: label, URL env var + default URL, password env var — names only, never values |
| `config/routing.toml` | `RoutingConfig` / `ProviderSpec` | LLM providers (model, `model_env`, key env, `keyless`, rpm, timeout) and tiers |
| `config/project.toml` | — | `default_pack`, `default_tenant` (used when a CLI or task doesn't name one) |
| `packs/*/plans/*.toml` | `PlanModel` | nodes and rules (see `agentkit_graph.md`) |
| `packs/*/tasks/*.toml` | `TaskDef` | harness tasks (see `agentkit_harness.md`) |
| `packs/*/fixtures/*.json` | `SeatFixture` | captured platform exchanges |

Why pydantic here and not elsewhere: these files are edited by people and read at a boundary, so a typo
(`pasword_env`, `modle`, a node key `tool`) should stop the program with the exact field path
(`nodes.0.tool: Extra inputs are not permitted`) instead of being silently ignored. Internal runtime objects
(graph nodes, results, run records) stay plain dataclasses.

`.env` (git-ignored) holds the values: `AS_EMAIL`, `AS_SURYODAYA_PASSWORD`, `AS_KEYSTONE_PASSWORD`, provider
keys, optional `*_MODEL` overrides, `AS_INSECURE_TLS=1` (platform TLS opt-out). `load_env()` loads it once
without overriding variables already set; importing `as_client` also loads it.
