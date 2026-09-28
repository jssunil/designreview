"""
Design-review graph actions: the complete allowlist of what a plan or the
planner may do on this seat. All are reads; there is no write action yet,
so a plan cannot change platform data at all.

Tool names and argument shapes are the live seat catalogue (Entity.verb,
flat kwargs), confirmed 2026-09-28. `endpoint.designreview.compare`,
`measure` and `upload` are no longer offered to this seat, so nothing here
relies on them.
"""

from __future__ import annotations

from typing import Any, Dict

from agentkit.graph import RESOLVED, RunEnv, declined
from agentkit.registry import Registry
from agentkit.retrieval import EvidenceIndex, chunk_by_topic, plain_text

# Computed endpoints this pack calls that are reads despite not ending in
# .list/.get -- the journal and the dry-run policy check rely on this list.
READ_ONLY_ENDPOINTS = (
    "endpoint.designreview.release_readiness",
    "endpoint.designreview.release_impact",
    "endpoint.designreview.release_impact.get",
    "endpoint.designreview.parts_search",
    "endpoint.designreview.supplier_response_sla",
)

LIST_LIMIT = 50


def register_actions(reg: Registry) -> None:
    reg.declare_read_only(READ_ONLY_ENDPOINTS)

    @reg.action("load_design_file", retries=2, description="DesignFile.get for one file.")
    def load_design_file(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return env.client.invoke_tool("DesignFile.get", {"id": args["file_id"]})

    @reg.action("list_revisions", retries=2,
                description="Every DesignVersion of a file, oldest first, with commit messages.")
    def list_revisions(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return env.client.invoke_tool("DesignVersion.list", {
            "file_id": args["file_id"], "sort_by": "version_number", "sort_order": "asc",
            "limit": args.get("limit", LIST_LIMIT)})

    @reg.action("read_release_gate", retries=2,
                description="Aggregated release-readiness evidence (ready, reason codes, blockers).")
    def read_release_gate(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return env.client.invoke_tool("endpoint.designreview.release_readiness", {"file_id": args["file_id"]})

    @reg.action("list_feedback", retries=2, description="DesignFeedback rows for a file.")
    def list_feedback(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return env.client.invoke_tool("DesignFeedback.list", {"file_id": args["file_id"],
                                                              "limit": args.get("limit", LIST_LIMIT)})

    @reg.action("list_failed_checklists", retries=2,
                description="DesignChecklistResult rows for a file whose overall result is fail.")
    def list_failed_checklists(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return env.client.invoke_tool("DesignChecklistResult.list", {
            "file_id": args["file_id"], "overall_result": "fail", "limit": args.get("limit", LIST_LIMIT)})

    @reg.action("probe_tool_offered",
                description="Refuse up front when the tool a request needs is not in this seat's catalogue.")
    def probe_tool_offered(env: RunEnv, args: Dict[str, Any], results) -> Any:
        tool = args["tool"]
        if not env.client.offers(tool):
            return declined(f"{tool} is not available to this seat (absent from tools/list)",
                            data={"tool": tool, "offered": False}, error_kind="not_in_seat")
        return {"tool": tool, "offered": True}

    @reg.action("decline", description="A refusal decided at planning time; args carry the reason.")
    def decline(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return declined(args.get("reason", "request declined"), data=dict(args))


# ---------------------------------------------------------------- DFM standards retrieval

STANDARDS_PAGE = 100
STANDARDS_MAX_PAGES = 10
STANDARDS_TOP_K = 5


def _all_rows(client, tool: str, args: Dict[str, Any], page: int = STANDARDS_PAGE,
              max_pages: int = STANDARDS_MAX_PAGES) -> list:
    """Page a .list tool through env.client.invoke_tool, so every page is journaled."""
    rows, offset = [], 0
    for _ in range(max_pages):
        payload = client.invoke_tool(tool, {**args, "limit": page, "offset": offset})
        batch = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(batch, list):
            break
        rows.extend(r for r in batch if isinstance(r, dict))
        offset += len(batch)
        total = payload.get("total")
        if not batch or len(batch) < page or (total is not None and offset >= int(total)):
            break
    return rows


def standards_query(results) -> str:
    """What to look standards up by: the change notes being reviewed plus the
    release blockers' titles (when the gate has already answered)."""
    parts = []
    rev = results.get("revisions")
    if rev is not None and rev.verdict == RESOLVED and isinstance(rev.data, dict):
        rows = sorted((r for r in rev.data.get("data") or [] if isinstance(r, dict)),
                      key=lambda r: r.get("version_number") or 0)
        parts += [r.get("commit_message") or "" for r in rows[-2:]]
    gate = results.get("release_gate")
    if gate is not None and gate.verdict == RESOLVED and isinstance(gate.data, dict):
        g = gate.data.get("result", gate.data)
        parts += [b.get("title") or "" for b in g.get("blockers") or [] if isinstance(b, dict)]
    return " ".join(p for p in parts if p)


def register_retrieval(reg: Registry) -> None:
    @reg.action("gather_dfm_standards", retries=2,
                description="Index the company's DesignStandard records and recall those relevant to the change.")
    def gather_dfm_standards(env: RunEnv, args: Dict[str, Any], results) -> Any:
        rows = _all_rows(env.client, "DesignStandard.list", {})
        index = EvidenceIndex()
        for r in rows:
            body = "\n".join(x for x in (r.get("name") or "", plain_text(r.get("description") or "")) if x)
            meta = {k: r.get(k) for k in ("name", "category", "standard_body", "standard_number", "is_active")}
            # Active records first, so the per-name dedupe below keeps an active one when ranks tie.
            meta["_rank"] = 0 if r.get("is_active") in (1, True) else 1
            index.add(chunk_by_topic(body, source_id=r.get("id") or "", source_entity="DesignStandard", meta=meta))
        query = args.get("query") or standards_query(results)
        categories = args.get("categories")
        # Seed data repeats standard names across records; one hit per base name.
        for p in index.passages:
            p.meta["base_name"] = (p.meta.get("name") or "").split("—")[0].strip().lower()
        hits = index.recall(query, k=args.get("k", STANDARDS_TOP_K),
                            where={"category": categories} if categories else None, distinct_by="base_name")
        return {"query": query, "indexed_records": len(rows), "indexed_passages": len(index),
                "matches": [{"id": h.passage.source_id,
                             **{k: v for k, v in h.passage.meta.items() if not k.startswith("_") and k != "base_name"},
                             "score": h.score,
                             "matched_terms": h.matched, "passage": h.passage.text[:300]} for h in hits]}
