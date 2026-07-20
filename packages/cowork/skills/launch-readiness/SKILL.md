---
name: launch-readiness
description: Coordinate the Agent IQ EU Cold-Water Launch Command Center. Use for cross-functional go/no-go decisions spanning formulation, brand comparison, demand, manufacturing, and regulatory readiness.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# EU Cold-Water Launch Command Center

## Outcome

Produce a cited conditional go/no-go recommendation from the minimum justified
cross-functional squad. The full scenario expects these five heroes to be
discovered from the work, not named in the routing request:

- `consumer-formulation-rd`
- `consumer-brand-to-brand-compare`
- `consumer-demand-forecasting`
- `consumer-manufacturing-operations`
- `consumer-regulatory-affairs`

## Workflow

1. Confirm brand/SKU, formula version, market, launch date, plants, decision
   owner, and whether the user wants analysis only or an approval package.
2. Use Cowork's built-in Work IQ capability to retrieve yesterday's launch
   review, relevant email decisions, approved files, action owners, and existing
   approval state. Security-trim before selection.
3. Create one `ContextReference` per selected source:
   - `kind: work_iq`;
   - caller and tenant must match the command;
   - include URI, permission scope, sensitivity, relevance terms, and approved
     status only;
   - do not copy source content or unrelated M365 context.
4. Build the orchestration intent as explicit work items. For a sequential
   decision package, use:

   > Validate the documented BrightWash cold-water enzyme stability evidence for a
   > German launch on 2026-10-01, then benchmark BrightWash versus ClearWave cold-water
   > brand differences using available non-market-specific evidence, then
   > forecast Germany launch demand for SAP product brightwash-cold from
   > 2026-10-01 through 2026-10-31, then verify capacity and OEE at SAP plant
   > DE-PLANT-FIXTURE-01 and line LINE-FIXTURE-01 for the same period, then
   > determine whether EU Regulation 1223/2009 registration applies before
   > launch.

   Preserve user-specific date and SKU constraints without weakening those five
   work items.
5. For this read-only workflow, call `orchestrate` once. It performs discovery,
   selection, planning, and invocation. Do not call `search_agents` as a routine
   preflight; use it only when the user explicitly asks to inspect alternatives
   before execution.
6. Do not announce a squad and then narrate a separate dispatch. Show one
   concise formation reason while work runs. Mention northbound MCP and
   southbound native A2A only when the user asks for transport details.
7. Show the returned progress messages without raw event codes, file searches,
   or spill recovery narration. Use the compact terminal result directly:
   `selectedAgents`, `answer`, `citations`, `sourcesMarkdown`, `issues`, and
   `nextAction`. Do not call `get_execution`, search for spill files, or
   reconstruct the answer after `orchestrate` returns a terminal result. Use
   `get_execution` only when the user explicitly requests audit details for an
   execution ID. Use `cancel_execution` only on user request.

## Evidence planes

- **Work IQ:** permission-trimmed M365 source references selected by Cowork.
- **Foundry IQ:** technical and regulatory evidence returned by hosted agents.
- **SAP:** read-only operational objects only when IDs, freshness, authorization
  provenance, and live/fixture status are returned.
- **Fabric IQ / Web IQ:** include only when a real, freshness-stamped connector
  result is present. Otherwise label the plane unavailable; never simulate it
  silently.

No SAP write, Copilot Studio approval, Autopilot handoff, Teams callback,
PowerPoint, or Excel artifact is implemented by this skill. List each as
a next gated step rather than claiming it occurred.

## Decision package

Return:

1. **Recommendation:** go, conditional go, no-go, or insufficient evidence.
2. **Conditions:** owner, due date, evidence, and approval required.
3. **Squad:** every selected agent, work item, public reason, and step status.
4. **Readiness:** formulation, brand, demand, plant, and regulatory sections.
5. **Conflicts and gaps:** never hide partial or declined work.
6. **Approval boundary:** analysis is not authorization to mutate SAP or launch.
7. **Sources:** preserve global markers and render the shared list once.

If any critical plane is unavailable, issue a conditional decision or honest
decline. Never produce success-shaped fallback.

Do not promise to remember a squad correction unless an authenticated,
user-scoped preference store confirms it. Use only caller metadata supplied by
an authenticated host; otherwise do not claim caller-bound authorization.
