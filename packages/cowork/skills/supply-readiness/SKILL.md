---
name: supply-readiness
description: Assess demand and manufacturing readiness through a dynamically selected Agent IQ squad. Use for forecasts, inventory, production orders, OEE, plant capacity, material availability, or launch supply risk.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Supply readiness

## Outcome

Produce a conditional readiness decision with demand and plant constraints kept
separate. Typical selection includes `consumer-demand-forecasting` and
`consumer-manufacturing-operations`, but discovery and dependency planning remain
Agent IQ responsibilities.

## Workflow

1. Confirm SKU, market, launch window, demand horizon, plant, and decision
   threshold. Do not assume missing operational keys.
2. Use Cowork's built-in Work IQ capability for relevant forecast approvals,
   launch-review decisions, owner assignments, and files. Select only items the
   caller can access.
3. Pass selected M365 items as approved, caller-bound `ContextReference` objects
   with `kind: work_iq`; pass no source body.
4. For this read-only workflow, call `orchestrate` once. It already performs
   discovery, selection, and invocation. Do not call `search_agents` as a
   routine preflight; use it only when the user explicitly asks to inspect
   alternatives before execution.
5. Use independent clauses when demand and capacity can be assessed in parallel:

   > Forecast German launch demand and verify plant OEE capacity.

   Use explicit ordering when capacity must consume the forecast:

   > Forecast German launch demand, then verify plant OEE capacity against that
   > forecast.

6. Do not announce a squad and then narrate a separate dispatch. Show one
   concise squad reason while work runs. Mention northbound MCP and southbound
   native A2A only when the user asks for transport details.
7. Show the returned progress messages without raw event codes, file searches,
   or spill recovery narration. Use the compact terminal result directly:
   `selectedAgents`, `answer`, `citations`, `sourcesMarkdown`, `issues`, and
   `nextAction`. Do not call `get_execution`, search for spill files, or
   reconstruct the answer after `orchestrate` returns a terminal result. Use
   `get_execution` only when the user explicitly requests audit details for an
   execution ID.

## Operational evidence

Demand and manufacturing agents may use governed read-only SAP tools. Treat an
object as live only when the result includes its SAP object ID, authorization
provenance, and freshness metadata. Data labelled `NOT LIVE SAP` is a
deterministic fixture and must be displayed as such.

Never request or imply an SAP mutation. A production order, allocation, or
inventory change requires a separate human approval and write-capable milestone.

## Response contract

Return:

1. **Demand position** - horizon, assumptions, range, and confidence.
2. **Supply position** - plant, capacity/OEE, materials, and open constraints.
3. **Decision** - ready, conditionally ready, not ready, or evidence unavailable.
4. **Risks and owners** - only owners supported by selected Work IQ references.
5. **Selected squad and lifecycle** - public reasons and each step status.
6. **Sources** - preserve markers and render the unified list exactly once.

Do not merge an unavailable demand result into a successful capacity claim. Show
partial execution and blocked dependencies explicitly.

Do not promise to remember a squad correction unless an authenticated,
user-scoped preference store confirms it. If authenticated caller metadata is
unavailable, do not claim caller-bound authorization. Do not present
fixture-backed operational facts as live SAP.
