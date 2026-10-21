+++
id = "pb-14"
title = "Daily digest of blocked release gates"
status = "spec"
questions = ["Each morning, list the design files whose release gate has blockers."]
tools = ["DesignFile.list", "endpoint.designreview.release_readiness"]
tasks = []
triggers = ["scheduled:daily"]
writes = false
+++

# PB-14 · Daily digest of blocked release gates

## Use case

A design lead wants one list each morning, without asking for it.

## Design

A scheduled run reads every file in a review state and its release gate. It lists the files with blockers, the new blockers since yesterday, and the blockers that cleared.

## Limits

The trigger is ready: a cron job or an n8n schedule can ask the agent service (`POST /v1/ask` or a signed hook, see `docs/explainers/agentkit_service.md`). The plan is not ready: it needs a list of all files in a review state and a store for yesterday's list.
