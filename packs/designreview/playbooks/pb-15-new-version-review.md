+++
id = "pb-15"
title = "Review each new version on commit"
status = "spec"
questions = ["When a new version of a design file is committed, check it and tell the owner."]
tools = ["DesignVersion.list", "endpoint.designreview.release_readiness"]
tasks = []
triggers = ["event:DesignVersion.created"]
writes = false
+++

# PB-15 · Review each new version on commit

## Use case

The engineer gets the PB-01 answer as soon as someone commits a version, not only when they ask.

## Design

An event from the platform starts the PB-01 plan for the new version and the version before it. The answer goes to the file owner.

## Limits

The event name is a placeholder: the seat has no event subscription tool today. Another system can send the event to the signed hook `new_version` of the agent service (`config/hooks.toml`), and the agent then answers as for PB-01. The agent has no write action to send the answer to the owner. That needs a reviewed write path like PB-02's.
