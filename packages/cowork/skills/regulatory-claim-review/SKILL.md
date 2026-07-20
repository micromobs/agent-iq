---
name: regulatory-claim-review
description: Review formula-supported claims and market requirements through Agent IQ. Use for EU 1223/2009, CPNP, PIF, ingredient restrictions, registration evidence, or claim substantiation.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Regulatory claim review

## Outcome

Return a market-specific claim decision that keeps technical substantiation and
regulatory interpretation traceable. A combined request commonly selects
`consumer-formulation-rd` followed by `consumer-regulatory-affairs`; never hard-code that
route.

## Workflow

1. Confirm product, formula/version, exact claim text, market, target date, and
   requested decision.
2. Use Cowork's built-in Work IQ capability to locate only the relevant formula
   decision, claim draft, prior review, and accountable owner that the user can
   access.
3. Convert those items to caller-bound `ContextReference` objects using
   `kind: work_iq`. Preserve source links and permissions; do not copy source
   content into the command.
4. For this read-only workflow, call `orchestrate` once. It performs discovery,
   selection, and invocation. Do not call `search_agents` as a routine preflight;
   use it only when the user explicitly asks to inspect alternatives before
   execution.
5. When technical evidence must precede regulatory review, make the dependency
   explicit:

   > Validate BrightWash cold-water enzyme claim support, then check EU 1223/2009
   > registration and claim requirements for Germany.

6. Do not announce selection and then narrate a separate dispatch. Show one
   concise squad reason while work runs. Mention northbound MCP and southbound
   native A2A only when the user asks for transport details.
7. Show the returned progress messages without raw event codes, file searches,
   or spill recovery narration. Use the compact terminal result directly:
   `selectedAgents`, `answer`, `citations`, `sourcesMarkdown`, `issues`, and
   `nextAction`. Do not call `get_execution`, search for spill files, or
   reconstruct the answer after `orchestrate` returns a terminal result. Use
   `get_execution` only when the user explicitly requests audit details for an
   execution ID.

## Decision rules

- Separate formula facts, proposed wording, legal requirement, and reviewer
  judgment.
- Do not convert missing evidence into approval.
- Do not claim legal advice, regulator acceptance, CPNP submission, or PIF
  completion unless the returned evidence explicitly supports it.
- Conflicting grounded sources produce a conditional or blocked decision, not a
  fabricated consensus.
- This skill has no registration or SAP write capability.

## Response contract

Return:

| Claim | Technical support | Market requirement | Decision | Required action |
|---|---|---|---|---|

Then show:

- selected agents and public reasons;
- dependency and lifecycle status;
- explicit evidence gaps and conflicts;
- approval boundary and accountable next action;
- unchanged inline citation markers and one shared **Sources** list.

Use `supported`, `conditional`, `unsupported`, or `insufficient evidence` for
each claim. An honest decline is the correct result when no eligible grounded
agent can support the decision.

Do not promise to remember a squad correction unless an authenticated,
user-scoped preference store confirms it. Identity values not supplied by an
authenticated host are not proof of user authorization. Only references already
security-trimmed by Cowork may be supplied.
