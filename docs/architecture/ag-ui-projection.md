# Governed AG-UI projection

Agent IQ keeps `contracts/events.py` as its canonical lifecycle ledger. AG-UI is
a one-way presentation projection for rich internal hosts; it is not a planning,
policy, evidence, or audit authority.

The wire subset is pinned to `ag-ui-protocol/ag-ui` commit
`e0d48fd100c15448553cf4ef91218f4d0bbfff29`. The Python core implements strict,
language-neutral contracts without adding an AG-UI runtime dependency.

## Mapping

| Canonical event | Safe AG-UI projection |
|---|---|
| `request.accepted` | `RUN_STARTED`, initial `STATE_SNAPSHOT`, governed activity |
| `catalog.searched` | `STATE_DELTA` with count/version, governed activity |
| `candidates.ranked` | Public candidate score/state delta, governed activity |
| `agents.selected` | Public selection facts/state delta, governed activity |
| `plan.prepared` | Content-safe DAG shape/state delta, governed activity |
| `approval.requested/resolved` | Step-bound approval state and governed activity |
| `invocation.started` | State delta, `STEP_STARTED`, argument-free `TOOL_CALL_START` |
| `invocation.delta` | Assistant text start/content |
| `evidence.received` | Evidence-ID state delta and governed activity |
| `invocation.completed/failed` | State delta, text/tool/step end, governed activity |
| `synthesis.completed` | Final answer text events plus citation-number state |
| `task.handed_off/completed` | Destination-free durable-task state/activity |
| `execution.completed` | Terminal state/activity and `RUN_FINISHED` or `RUN_ERROR` |

Every projected event is wrapped with the canonical event ID, sequence,
execution ID, trace ID, and deterministic per-source projection index. The
projection can therefore be replayed, correlated, and compared without
replacing the immutable domain ledger.

## Content boundary

The projection deliberately excludes:

- intent and catalog query text;
- caller, tenant, request, work-item, and context identifiers;
- tool arguments and invocation input;
- private policy metadata;
- free-form candidate explanations, selection summaries, and approval reasons;
- provider error messages;
- source content, excerpts, and private source locators;
- AG-UI reasoning, thinking, encrypted-reasoning, raw, and custom event
  families.

Candidate and selection explanations use fixed governed wording. Structured
matched public fields, skills, tags, scores, and policy facts remain visible,
but free-form canonical explanations are not copied because they can repeat
request text. Plans expose only step and agent IDs, dependencies, context kinds,
output counts, invocation mode, concurrency, and approval requirements.
Approvals expose the governed step IDs and terminal decision with a fixed scope
summary. Evidence is represented by governed IDs; authorized detail resolution
remains a separate service operation.

Unknown canonical event classes, missing trace correlation, non-contiguous
sequences, and lifecycle events without a matching start fail closed. The
projector never emits a best-effort or raw-event fallback.

## Contracts and use

- Wire contract: `src/agent_iq/contracts/agui.py`
- Projector and replay: `src/agent_iq/presentation/agui.py`
- Published schema: `schemas/v1/ag-ui-projection.schema.json`
- Service access: `AgentIqService.get_agui_projection`
- HTTP access: `GET /v1/executions/{execution_id}/ag-ui?thread_id=...`

Canvas clients apply `STATE_SNAPSHOT` followed by RFC 6902 `STATE_DELTA`
operations. The current projection emits bounded top-level `replace`
operations, while preserving the standard patch-array wire shape.
