---
name: formulation-brief
description: Build a governed, evidence-backed formulation brief by routing the request through Agent IQ. Use for formula composition, enzyme stability, surfactants, cold-water performance, or technical claim support.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Formulation brief

## Outcome

Produce a concise technical brief grounded in approved Foundry IQ evidence. The
expected specialist is `consumer-formulation-rd`, but always let Agent IQ discover and
justify the selection rather than routing by agent ID.

## Workflow

1. Clarify the product, market, performance target, and decision needed. Ask one
   focused question if any of those would materially change the answer.
2. If the user refers to a meeting, email decision, file, or named owner, use
   Cowork's built-in Work IQ capability first. Select only the sources needed for
   this formulation decision.
3. Convert each selected M365 item into a `ContextReference`:
   - set `kind` to `work_iq`;
   - preserve its source URI, permission scope, sensitivity, caller subject, and
     tenant;
   - set `approved` only when the user is authorized to use it;
   - never copy email, meeting, chat, or file content into the reference.
4. For this read-only workflow, call `orchestrate` once. It performs discovery,
   selection, and invocation, so do not call `search_agents` as a routine
   preflight and do not force an agent ID. Use `search_agents` only when the user
   explicitly asks to inspect alternatives before execution.
5. Show the returned progress messages as the status. Do not add file searches,
   spill recovery, raw event codes, or a second dispatch narrative.
6. Use the compact terminal result directly: `selectedAgents`, `answer`,
   `citations`, `sourcesMarkdown`, `issues`, and `nextAction`. Do not call
   `get_execution`, search for spill files, or reconstruct the answer after
   `orchestrate` returns a terminal result. Use `get_execution` only when the
   user explicitly requests audit details for an execution ID.
7. Use `cancel_execution` only when the user requests cancellation.
8. Transport is diagnostic detail, not the headline. Mention northbound MCP and
   southbound native A2A only when the user asks how the work ran.

## Intent construction

Keep the intent specific and decision-oriented, for example:

> Validate BrightWash cold-water enzyme stability and prepare a formulation brief with
> supported performance claims for Germany.

Do not add competitor, demand, manufacturing, or regulatory work unless the user
asked for it. Agent IQ should select the minimum eligible squad.

## Response contract

- Name every selected agent and show its public selection reason.
- Distinguish lifecycle status from answer quality.
- Present formula findings, constraints, unsupported assumptions, and next
  evidence needed.
- Keep inline citation markers and render the returned shared **Sources** list
  without renumbering or inventing links.
- Surface partial, declined, failed, or timed-out steps explicitly.
- If no grounded evidence supports a material, stability, or performance claim,
  give an honest decline instead of filling the gap.
- Do not promise to remember a selection or preference unless an authenticated,
  user-scoped preference store confirms it.
- Use only caller and tenant identifiers supplied by an authenticated host.
  Never invent identity values or claim caller-bound authorization when they are
  unavailable.
- No SAP write or other business mutation is available from this skill.
