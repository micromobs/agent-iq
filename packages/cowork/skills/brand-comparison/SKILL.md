---
name: brand-comparison
description: Create a cited side-by-side brand comparison through Agent IQ. Use for BrightWash versus ClearWave, head-to-head positioning, formulation differences, evidence gaps, or competitor benchmarking.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Brand comparison

## Outcome

Return a structured, evidence-backed comparison without mixing brand claims or
fabricating competitive facts. The expected specialist for a pure comparison is
`consumer-brand-to-brand-compare`; Agent IQ must discover it from the intent.

## Workflow

1. Confirm the two brands, market, comparison dimensions, and decision. Keep
   `versus` in a single comparison clause so this remains one work item.
2. If the user cites a launch review, campaign brief, email, or file, use
   Cowork's built-in Work IQ capability. Retain only permission-trimmed source
   references relevant to the requested dimensions.
3. Represent M365 context as caller-bound `ContextReference` objects with
   `kind: work_iq`. Pass IDs, URIs, policy bindings, and relevance terms only;
   never place raw M365 content in the Agent IQ command.
4. For this read-only workflow, call `orchestrate` once. It already discovers,
   selects, and invokes the minimum squad. Do not call `search_agents` as a
   routine preflight; use it only when the user explicitly asks to inspect
   alternatives before execution.
5. Do not announce selection and then narrate a separate dispatch. Show one
   concise selection reason while work runs, then lead with the comparison.
   Mention northbound MCP and southbound native A2A only when the user asks for
   transport details.
6. Show the returned progress messages without raw event codes, file searches,
   or spill recovery narration. Use the compact terminal result directly:
   `selectedAgents`, `answer`, `citations`, `sourcesMarkdown`, `issues`, and
   `nextAction`. Do not call `get_execution`, search for spill files, or
   reconstruct the answer after `orchestrate` returns a terminal result. Use
   `get_execution` only when the user explicitly requests audit details for an
   execution ID.

## Intent construction

For a pure comparison, use one clause:

> Benchmark BrightWash versus ClearWave cold-water performance in Germany across enzyme
> system, evidence strength, and supported claims.

If the user separately asks for formulation design, make that a distinct clause
and allow Agent IQ to select a squad. Do not force either agent.

## Response contract

Use a side-by-side table with:

| Dimension | Brand A | Brand B | Evidence strength | Gap |
|---|---|---|---|---|

After the table:

- name selected agents and preserve their public selection reasons;
- identify conflicts rather than averaging incompatible claims;
- preserve returned citation markers and render the shared **Sources** section
  exactly once;
- show lifecycle and partial-failure status;
- distinguish absent evidence from evidence of no difference.
- do not promise to remember a selection unless an authenticated, user-scoped
  preference store confirms it.

If either brand lacks grounded evidence for a requested dimension, state
`insufficient grounded evidence` for that cell. Do not infer confidential
competitor composition.

Use only caller values supplied by an authenticated host; otherwise do not claim
caller-bound authorization. This skill performs no write action and must not
claim live Web IQ freshness unless a freshness-stamped source is actually
returned.
