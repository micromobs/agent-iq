---
name: orchestrate-agents
description: Discover and execute the minimum eligible AI agent squad through Agent IQ. Use for single-agent or multi-agent requests that need governed evidence and citations.
allowed-tools:
  - agent-iq/search_agents
  - agent-iq/orchestrate
  - agent-iq/capabilities
  - workiq/ask
metadata:
  version: "1.0.0"
  status: canary
---

# Orchestrate agents

Contract version: `1.0.0`.

Use this skill for read-only analysis that Agent IQ can route to one or more
eligible A2A specialists.

## Build the command

Create one `OrchestrationCommand`:

- `request.requestId`: a unique, non-sensitive identifier;
- `request.intent`: the user's bounded, decision-oriented intent;
- `request.caller.subjectId` and `tenantId`: authenticated host values when
  available, otherwise visibly synthetic canary values;
- `request.requestedAt`: the current UTC timestamp;
- `contextReferences`: only authorized metadata references selected for this
  request.

If the user refers to Microsoft 365 context and `workiq/ask` is available, ask
Work IQ for the smallest sufficient set of source links and metadata. A
`ContextReference` may contain a source URI, permission scope, sensitivity,
relevance terms, and approval state. It must not contain mail, meeting, file,
chat, or people content. Its caller and tenant must match the request.

## Run

1. Call `agent-iq/orchestrate` once for normal read-only work.
2. Do not call `search_agents` first. The orchestration call already performs
   security-trimmed matching and selects the minimum eligible squad.
3. Call `agent-iq/search_agents` only when the user explicitly requests a
   preview of alternatives. A preview does not authorize invocation.
4. Do not force a known demo agent ID into the request. Catalog metadata and
   policy determine the squad.
5. Use the compact terminal result directly. Do not reconstruct the answer from
   lifecycle fragments or invoke a selected agent through another route.

## Present

Show the answer, terminal status, selected agents with public reasons, visible
issues, citations, Sources, and next action. Preserve global citation numbers
and links. State unavailable evidence or systems plainly. Never turn a failed
or declined step into success-shaped prose.

Never expose hidden reasoning, private queries, caller or context identifiers,
tool arguments, policy internals, provider messages, or raw canonical events.
Do not promise preference memory. The connector is an anonymous,
non-production canary and cannot perform a business-system mutation.
