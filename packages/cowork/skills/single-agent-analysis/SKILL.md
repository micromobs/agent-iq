---
name: single-agent-analysis
description: Run a focused Agent IQ one-agent formulation analysis in Microsoft Cowork. Use when the user asks to show a real agent call, call one Foundry agent, inspect discovery and selection, or compare BrightWash and ClearWave cold-water enzyme systems.
license: MIT
metadata:
  author: Agent IQ
  version: "1.2.0"
---

# Focused single-agent Foundry analysis

## Outcome

Run one short, reliable, end-to-end workflow in which Cowork calls Agent IQ through
northbound MCP, Agent IQ performs real catalog discovery and ranking, exactly one
specialist is selected, and the selected hosted Microsoft Foundry agent is invoked
through southbound native A2A. The default hero is
`consumer-formulation-rd`, answering a grounded BrightWash-versus-ClearWave
cold-water enzyme question with citations.

This is not a static response, simulated card, or direct knowledge-base lookup. The
tool runs the real discovery, selection, orchestration, Foundry invocation, evidence,
and synthesis path.

## Trigger phrases

Use this skill immediately when the user says any of the following:

- "Run a focused Agent IQ analysis."
- "Show me one real agent call."
- "Call a single Foundry agent."
- "Show discovery and selection of one agent."
- "Which fifth enzyme differentiates ClearWave from BrightWash?"
- "Show Agent IQ working in Cowork."

Do not ask the user to choose an agent for the default analysis.

## One-tool workflow

1. Call `run_single_agent_demo` exactly once.
2. For the standard analysis, omit `question` so the connector uses its proven default:
   "What four enzymes power BrightWash Cold, and which fifth enzyme differentiates
   ClearWave Cold Clean? Cite both product sources."
3. If the user supplied a different concise domain question, pass that text as
   `question`. The tool still sets `maxSelected=1`, so Agent IQ selects exactly one
   eligible specialist.
4. Do not call `search_agents` first. The single tool already performs real catalog
   discovery and exposes the selected agent and public selection reason.
5. Do not call the lower-level `orchestrate` tool before or after the analysis. It requires
   the full command contract and is intended for advanced workflows; the dedicated
   tool creates that contract safely.
6. Do not call `get_execution`, search for spill files, or reconstruct the answer
   after a terminal result. Use `get_execution` only when the user explicitly
   requests audit details for an execution ID.

The default analysis intentionally uses no Work IQ content and no `ContextReference`.
That keeps the first run focused on the agent platform itself. Work IQ and approved
`ContextReference` inputs remain available for advanced workflow skills.

## What the tool proves

The response must make this actual runtime chain visible:

1. Cowork selected the Agent IQ skill.
2. Cowork called the remote MCP connector.
3. Agent IQ searched and ranked the live agent catalog.
4. Agent IQ selected exactly one eligible specialist.
5. Agent IQ invoked the selected hosted Foundry agent using native A2A version 1.0.
6. The specialist returned structured evidence.
7. Agent IQ synthesized a grounded answer with shared citations and Sources.

For the default prompt, the selected agent should be
`consumer-formulation-rd`. If a different agent is returned, do not hide or rewrite
the result; state that the live selector chose a different specialist and show the
public reason.

## Response contract

Use the compact result returned by `run_single_agent_demo`:

- `selectedAgents`: show the one selected specialist, display name, and reason.
- `answer`: render the grounded answer without inventing or rewriting claims.
- `citations`: preserve every citation number, URL, and evidence excerpt. The MCP App
  uses the excerpts to build the enzyme-flow visualization; do not summarize them
  into unsupported facts.
- `sourcesMarkdown`: render this shared Sources block without renumbering.
- `issues`: show any partial, declined, failed, or timed-out condition plainly.
- `nextAction`: show it only when the connector returned one.

Recommended presentation:

> **Agent IQ analysis**
>
> **Discovered and selected:** <agent display name> (`<agent id>`)
>
> **Why selected:** <public selection reason>
>
> **Runtime:** Cowork -> remote MCP -> Agent IQ discovery -> native A2A ->
> Microsoft Foundry hosted agent
>
> <grounded answer with the returned citation markers>
>
> <returned sourcesMarkdown>

Keep the conversation response concise and let the MCP App be the primary visual:
it renders the enzyme contribution map, comparison insight, source pills, and the
selected Formulation agent card. Do not dump raw lifecycle events, tool arguments,
internal prompts, model reasoning, evidence excerpts, or connector credentials.

## Failure behavior

- If `selectedAgents` is empty or contains more than one item, say the one-agent analysis
  did not meet its selection contract. Do not present it as a success.
- If `issues` reports a failure, timeout, decline, or evidence gap, surface it.
- If there are no citations for a factual answer, say grounded evidence was
  unavailable.
- Be honest and do not invent, fabricate, or silently replace a failed live call with
  a canned answer.
- Never claim that a business write occurred. This workflow is read-only.

## Presenter follow-up

After the result, the best next prompt is:

> "Now show me why Agent IQ selected that specialist."

Answer from the returned `selectedAgents` reason. Call `search_agents` only if the
user explicitly asks to inspect the broader ranking or alternatives.
