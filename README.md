# AgentSwitch — Design Review Seat (`designreview`)

> **EAGv3 Capstone Project** | **Team 21**  
> **Assigned Seat:** Design Review (`designreview`) — Design Files, Versions, Reviews  
> **Core Request:** *"What changed between rev B and rev C, and are there manufacturability problems in this part?"*

---

## 📌 Project Overview

**AgentSwitch** is a multi-agent, enterprise-grade business operations platform spanning 424 entity types deployed across two simulated manufacturing enterprises:
1. **Suryodaya Precision Works** (India — GST / Ind AS)
2. **Keystone Precision Works LLC** (US — Sales & Use Tax / ASC)

Each team operates within an assigned **seat** with specific entity permissions, API scopes, and MCP interfaces. Our seat, **Team 21 (`designreview`)**, is tasked with building an autonomous, MCP-driven agent that can perform multi-step geometric/BOM revision comparisons, inspect design revisions, validate engineering checklists, and assess manufacturability (DFM/DFA) issues.

---

## 🎯 The Core Agent Goal

Our agent must handle the following high-bar engineering evaluation autonomously over Model Context Protocol (MCP) and REST APIs:

```text
"What changed between rev B and rev C, and are there manufacturability problems in this part?"
```

This involves:
- **CAD & BOM Revision Diffing**: Identifying structural, dimensional, and assembly changes between revisions (e.g., rev B vs. rev C).
- **Manufacturability & DFM Analysis**: Surfacing tooling constraints, draft angles, wall thickness deviations, hole-to-edge tolerances, and material callouts.
- **Checklist & Review Validation**: Cross-referencing findings against design validation checklists and supplier capability requests.

---

## 🏗️ Repository Structure & Key Documents

| File / Document | Purpose |
| :--- | :--- |
| **[`PLAN.md`](PLAN.md)** | Master architectural plan, competitive analysis strategy, and roadmap for deliverable execution. |
| **[`GAP_REPORT.md`](GAP_REPORT.md)** | Gap analysis comparing AgentSwitch's `designreview` capabilities with modern industry benchmark tools (e.g., CoLab Software / AutoReview). |
| **[`ENTITY_SCHEMAS.md`](ENTITY_SCHEMAS.md)** | Complete documentation of all 24 `designreview` entity types, including state machines (`DesignFile`, `DesignReview`, `DesignFeedback`, etc.). |
| **[`agentswitch.md`](agentswitch.md)** | Full specification and domain guide for the AgentSwitch platform. |
| **[`agentswitch_tools.json`](agentswitch_tools.json)** | Catalog of MCP tools exposed to our seat for agent orchestration. |
| **[`api_schemas.json`](api_schemas.json)** | Complete OpenAPI schemas for the platform endpoints. |
| **[`as_client.py`](as_client.py)** | Python client for authenticating, querying, and interacting with the AgentSwitch APIs across both tenant instances. |

---

## ⚙️ Workflow State Machines

The `designreview` seat manages 6 primary flow entities with strict state machines:

1. **`DesignFile`**: `uploaded` ➔ `processing` ➔ `ready` ➔ `approved` *(or `error`)*
2. **`DesignReview`**: `draft` ➔ `in_review` ➔ `approved` *(or `rejected` / `reopened`)*
3. **`DesignFeedback`**: `open` ➔ `in_progress` ➔ `resolved` *(or `dismissed` / `reopened`)*
4. **`DesignProject`**: `planning` ➔ `active` ➔ `on_hold` ➔ `completed` *(or `archived`)*
5. **`DesignSupplierRequest`**: `draft` ➔ `sent` ➔ `received` ➔ `accepted` *(or `rejected`)*
6. **`DesignShare`**: `active` ➔ `revoked` / `expired`

*(See [`ENTITY_SCHEMAS.md`](ENTITY_SCHEMAS.md) for full state transition diagrams and permission matrix)*

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- `gh` (GitHub CLI) & `git`
- Virtual environment tool (`venv` or `uv`)

### 2. Environment Setup
Create a `.env` file in the root directory (do not commit this):
```env
SURYODAYA_URL=https://agentswitch.theschoolofai.in
SURYODAYA_EMAIL=team21@theschoolofai.in
SURYODAYA_PASSWORD=your_password_here

KEYSTONE_URL=https://class.agentswitch.theschoolofai.in
KEYSTONE_EMAIL=team21@theschoolofai.in
KEYSTONE_PASSWORD=your_password_here
```

### 3. Quick Test with Python Client
```bash
python as_client.py
```
