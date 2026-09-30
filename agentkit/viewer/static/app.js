// Run viewer front end. No framework, no build.
// Every value from run data is inserted with textContent (via el()), never as HTML:
// the data holds platform text, and platform text must never become markup.
"use strict";

const app = document.getElementById("app");
const TABS = ["answer", "finding", "checks", "evidence", "tools", "truth", "cost"];
const TAB_LABELS = { answer: "Answer", finding: "Finding", checks: "Checks", evidence: "Evidence",
  tools: "Tool calls", truth: "Ground truth", cost: "Cost" };

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "href") node.setAttribute("href", safeHref(v));
    else if (k === "style_width") node.style.width = v;
    else if (k === "style_left") node.style.marginLeft = v;
    else node.setAttribute(k, String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Only in-page routes are ever linked.
function safeHref(v) { const s = String(v); return s.startsWith("#/") ? s : "#/"; }
function cls(value) { return "s-" + String(value || "").toLowerCase().replace(/[^a-z_]/g, ""); }
function badge(value) { return el("span", { class: "badge " + cls(value) }, value === null || value === undefined ? "—" : value); }
function runLink(id) { return el("a", { href: "#/run/" + encodeURIComponent(id) }, id); }
function fmt(n, digits) { return typeof n === "number" ? n.toFixed(digits === undefined ? 2 : digits) : "—"; }
function pretty(v) { return el("pre", {}, typeof v === "string" ? v : JSON.stringify(v, null, 2)); }
function table(headers, rows) {
  return el("div", { class: "table-wrap" },
    el("table", {}, el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c) => el("td", {}, c)))))));
}
function render(...nodes) { app.replaceChildren(...nodes.filter((n) => n !== null && n !== undefined && n !== false)); }

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// ---- pages ----------------------------------------------------------------

async function batchesPage() {
  const batches = await getJSON("/api/batches");
  if (!batches.length) return render(el("h1", {}, "Batches"), el("p", { class: "muted" }, "No batches yet. Run: python -m agentkit.harness.batch --grade"));
  render(el("h1", {}, "Batches"), table(["Batch", "Started", "Runs", "Status", "LLM", "Calibration"],
    batches.map((b) => [
      el("a", { href: "#/batch/" + encodeURIComponent(b.batch_id) }, b.batch_id),
      b.started_at || "—", b.runs,
      el("span", {}, Object.entries(b.status_counts).map(([s, n]) => el("span", {}, badge(s), " ", n, "  "))),
      b.skip_llm ? "skipped" : "on",
      b.calibration_ok === null ? "—" : badge(b.calibration_ok ? "ok" : "fail"),
    ])));
}

async function runsPage() {
  const runs = await getJSON("/api/runs");
  render(el("h1", {}, "All runs"), table(["Run", "Task", "Tenant", "Ended", "Score"],
    runs.map((r) => [runLink(r.run_id), r.task_id || "—", r.tenant || "—", badge(r.ended), badge(r.score_status)])));
}

async function batchPage(id) {
  const b = await getJSON("/api/batches/" + encodeURIComponent(id));
  const m = b.manifest || {};
  const parts = [el("h1", {}, "Batch " + b.batch_id),
    el("p", { class: "muted" }, `started ${m.started_at || "—"} · finished ${m.finished_at || "—"} · LLM ${m.skip_llm ? "skipped" : "on"} · ${m.write ? "WRITE" : "dry run"}`),
    table(["Task", "Tenant", "Score", "Checks", "Seconds", "Failing checks"], b.runs.map((r) => [
      runLink(r.run_id), r.tenant, badge(r.score_status || r.status),
      r.counts ? Object.entries(r.counts).map(([s, n]) => `${s} ${n}`).join(", ") : "—",
      fmt(r.seconds, 1),
      r.failing.length ? el("ul", {}, r.failing.map((c) => el("li", {}, badge(c.status), " ", `${c.check}/${c.name}: `, c.detail || ""))) : "—",
    ]))];
  const cal = b.calibration;
  if (cal) {
    parts.push(el("h2", {}, "Calibration ", badge(cal.ok ? "ok" : "fail")));
    const missed = cal.missed || [];
    const unexercised = cal.unexercised_checks || [];
    parts.push(el("p", {}, `${missed.length} missed mutant(s); ${unexercised.length} check(s) no mutant exercised`));
    if (missed.length || unexercised.length) parts.push(pretty({ missed, unexercised }));
    parts.push(table(["Mutant", "Target check", "Applied", "Caught", "Missed on"],
      Object.entries(cal.mutants || {}).map(([name, x]) => [name, x.target, x.applied, x.caught,
        (x.missed_on || []).length ? el("span", { class: "err" }, x.missed_on.join(", ")) : "—"])));
  }
  if (b.report) parts.push(el("h2", {}, "report.md"), pretty(b.report));
  render(...parts);
}

async function runPage(id, tab) {
  const r = await getJSON("/api/runs/" + encodeURIComponent(id));
  const tr = r.taskrun || {};
  tab = TABS.includes(tab) ? tab : "answer";
  const head = [el("h1", {}, r.run_id),
    el("p", {}, badge(tr.ended), " ", badge(r.score && r.score.status), " ",
      el("span", { class: "muted" }, `${tr.task_id || "—"} · ${tr.tenant || "—"} · ${tr.dry_run === false ? "WRITE" : "dry run"} · ${fmt(tr.seconds, 1)} s`)),
    tr.prompt ? el("p", {}, el("strong", {}, "Question: "), tr.prompt) : null,
    tr.error ? el("p", { class: "err" }, "Error: ", tr.error) : null,
    el("nav", { class: "tabs" }, TABS.map((t) => el("a", { href: `#/run/${encodeURIComponent(id)}/${t}`, class: t === tab ? "active" : null }, TAB_LABELS[t])))];
  const body = { answer: answerTab, finding: findingTab, checks: checksTab, evidence: evidenceTab,
    tools: toolsTab, truth: truthTab, cost: costTab }[tab](r);
  render(...head, body);
}

// ---- run tabs -----------------------------------------------------------------

function narrationDetail(tr) {
  const step = (tr.steps || []).find((s) => s.kind === "narrate");
  return (step && step.detail) || {};
}

function answerTab(r) {
  const tr = r.taskrun || {};
  const n = narrationDetail(tr);
  const audit = n.audit || {};
  const problems = ["unsupported_ids", "unsupported_quantities", "contradictions", "missing"]
    .filter((k) => (audit[k] || []).length).map((k) => el("li", {}, `${k}: `, JSON.stringify(audit[k])));
  return el("div", {},
    el("div", { class: "panel answer" }, tr.claimed_answer || el("span", { class: "muted" }, "(no answer)")),
    el("p", {}, "Source: ", badge(tr.answer_source || n.source), "  Audit: ", badge(audit.ok === undefined ? null : audit.ok ? "ok" : "fail"),
      n.fallback_reason ? el("span", { class: "muted" }, "  fallback: ", n.fallback_reason) : null),
    problems.length ? el("ul", {}, problems) : null,
    (n.attempts || []).length ? el("div", {}, el("h2", {}, "Attempts"), table(["#", "Chars", "Audit"],
      n.attempts.map((a, i) => [i + 1, a.chars === undefined ? (a.text || "").length : a.chars, badge(a.audit && a.audit.ok ? "ok" : "fail")]))) : null,
    (tr.warnings || []).length ? el("div", {}, el("h2", {}, "Warnings"), el("ul", {}, tr.warnings.map((w) => el("li", {}, w)))) : null);
}

// A generic tree for any JSON value: the viewer knows nothing about the pack's finding schema.
function tree(value, label, open) {
  if (value !== null && typeof value === "object") {
    const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
    const summary = `${label === undefined ? "" : label + ": "}${Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}`;
    return el("details", { open: open ? "" : null }, el("summary", { class: "kv" }, summary),
      entries.map(([k, v]) => tree(v, k, false)));
  }
  return el("div", { class: "kv" }, el("span", { class: "k" }, label === undefined ? "" : label + ": "),
    value === null ? "null" : String(value));
}

function findingTab(r) {
  const f = (r.taskrun || {}).finding;
  return f ? el("div", { class: "panel" }, tree(f, "finding", true)) : el("p", { class: "muted" }, "No finding recorded.");
}

function checksTab(r) {
  const s = r.score;
  if (!s) return el("p", { class: "muted" }, "Not graded yet. Run: python -m agentkit.harness.grade <batch dir>");
  return el("div", {}, el("p", {}, "Overall ", badge(s.status), el("span", { class: "muted" }, "  scorer ", s.scorer || "")),
    table(["Check", "Name", "Status", "Detail"], (s.checks || []).map((c) => [c.check, c.name, badge(c.status), c.detail || ""])));
}

function evidenceTab(r) {
  const nodes = ((r.graph || {}).nodes) || [];
  if (!nodes.length) return el("p", { class: "muted" }, "No graph recorded.");
  const starts = nodes.map((n) => n.started_at).filter((x) => typeof x === "number");
  const ends = nodes.map((n) => n.finished_at).filter((x) => typeof x === "number");
  const t0 = starts.length ? Math.min(...starts) : 0;
  const span = Math.max(1e-6, (ends.length ? Math.max(...ends) : t0) - t0);
  return el("div", {}, el("div", { class: "table-wrap" }, el("table", { class: "timeline" },
    el("thead", {}, el("tr", {}, ["Node", "Action", "Verdict", "After", "Added by", "Tries", "Time", "Reason"].map((h) => el("th", {}, h)))),
    el("tbody", {}, nodes.map((n) => {
      const res = n.result || {};
      const ok = typeof n.started_at === "number" && typeof n.finished_at === "number";
      const left = ok ? ((n.started_at - t0) / span) * 100 : 0;
      const width = ok ? Math.max(0.5, ((n.finished_at - n.started_at) / span) * 100) : 0;
      return el("tr", {}, el("td", {}, n.id), el("td", {}, n.action), el("td", {}, badge(res.verdict)),
        el("td", {}, (n.after || []).join(", ") || "—"), el("td", {}, n.added_by || "—"), el("td", {}, n.attempts),
        el("td", { class: "bar-cell" }, ok ? el("div", {}, el("div", { class: "bar", style_width: Math.min(width, 100 - left) + "%", style_left: left + "%" }),
          el("span", { class: "muted" }, `${fmt(n.finished_at - n.started_at)} s @ ${fmt(n.started_at - t0)} s`)) : "not run"),
        el("td", {}, res.reason || res.error_kind || ""));
    })))), el("p", { class: "muted" }, `Graph format ${(r.graph || {}).format || "—"}; bars are placed on the run's own clock.`));
}

function toolsTab(r) {
  const calls = r.journal || [];
  if (!calls.length) return el("p", { class: "muted" }, "No tool calls journaled.");
  const writes = calls.filter((c) => c.read === false).length;
  return el("div", {}, el("p", {}, `${calls.length} call(s); `, writes ? el("span", { class: "err" }, `${writes} write(s)`) : "reads only"),
    table(["#", "Tool", "Kind", "OK", "Error", "Seconds", "Args"], calls.map((c) => [
      c.seq, c.tool, c.read === false ? el("span", { class: "err" }, "write") : "read", badge(c.ok ? "ok" : "fail"),
      c.error_kind || "", fmt(c.seconds), el("span", { class: "kv" }, JSON.stringify(c.args || {}))])));
}

function truthTab(r) {
  const rows = r.ground_truth_changes || [];
  if (!rows.length) return el("p", { class: "muted" }, "No ground truth captured (runs outside a batch have none).");
  const changed = rows.filter((x) => x.changed).length;
  const obsB = ((r.before || {}).observations) || {};
  const obsA = ((r.after || {}).observations) || {};
  return el("div", {}, el("p", {}, `${rows.length} observation(s); `, changed ? el("span", { class: "changed" }, `${changed} changed during the run (drift)`) : "none changed during the run",
      el("span", { class: "muted" }, `  before ${(r.before || {}).observed_at || "—"} · after ${(r.after || {}).observed_at || "—"}`)),
    rows.map((x) => el("details", {}, el("summary", { class: x.changed ? "changed" : null }, x.key, x.changed ? "  (changed)" : "",
        x.after_error ? `  error: ${x.after_error}` : ""),
      x.changed ? el("div", {}, el("strong", {}, "Before"), pretty(obsB[x.key]), el("strong", {}, "After"), pretty(obsA[x.key]))
        : pretty(obsA[x.key]))));
}

function costTab(r) {
  const l = r.ledger;
  if (!l || !(l.calls || []).length) return el("p", { class: "muted" }, "No LLM calls (template answer or --skip-llm).");
  const s = l.summary || {};
  return el("div", {}, el("p", {}, `${s.calls} call(s) · ${s.input_tokens} in / ${s.output_tokens} out tokens · $${fmt(s.cost_usd_estimated, 4)} estimated`),
    table(["Provider", "Model", "In", "Out", "Cost $", "Latency s", "Stop", "Usage"], l.calls.map((c) => [
      c.provider, c.model, c.input_tokens, c.output_tokens, fmt(c.cost_usd, 4), fmt(c.latency_sec),
      badge(c.stop_reason), c.usage_estimated ? "estimated" : "reported"])));
}

// ---- router ---------------------------------------------------------------------

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  try {
    if (parts[0] === "batch" && parts[1]) await batchPage(parts[1]);
    else if (parts[0] === "run" && parts[1]) await runPage(parts[1], parts[2]);
    else if (parts[0] === "runs") await runsPage();
    else await batchesPage();
  } catch (err) {
    render(el("h1", {}, "Not available"), el("p", { class: "err" }, String(err.message || err)), el("a", { href: "#/" }, "Back to batches"));
  }
}

window.addEventListener("hashchange", route);
route();
