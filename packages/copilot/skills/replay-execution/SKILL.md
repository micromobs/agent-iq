---
name: replay-execution
description: Inspect and summarize a known Agent IQ execution without rerunning it. Use when the user asks for status, audit history, selected agents, evidence, or replay by execution ID.
allowed-tools:
  - agent-iq/get_execution
  - agent-iq/capabilities
metadata:
  version: "1.0.0"
  status: canary
---

# Replay an Agent IQ execution

Contract version: `1.0.0`.

This is a read-only replay. Require an Agent IQ `executionId`, then call
`agent-iq/get_execution` exactly once. Do not call `orchestrate`, do not repeat
the user's task, and do not invoke any specialist.

## Safe replay view

Render a concise timeline from immutable event sequence and timestamps:

1. request accepted;
2. eligible catalog candidates ranked;
3. minimum squad selected;
4. each specialist started and reached its terminal status;
5. governed evidence received;
6. synthesis and execution completed.

Include terminal status, duration, selected agents with public selection
summaries, per-agent status, evidence identifiers, final answer, citations, and
typed issue codes when present. Make partial, declined, failed, and cancelled
work visible.

Do not display intent or catalog query text from lifecycle payloads. Do not
display request, caller, tenant, work-item, or context identifiers. Do not
display tool arguments, private policy facts, source excerpts, raw provider
messages, or arbitrary event payloads. Never expose hidden reasoning. The safe
AG-UI presentation is a projection of the canonical ledger, not a replacement
for it.

If the execution is unavailable, report that directly. Do not fabricate a
timeline and do not silently start a replacement execution. This anonymous
canary has no cross-user authorization guarantee and must remain limited to
test data and test users until OAuth/OBO is complete.
