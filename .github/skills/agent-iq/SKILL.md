---
name: agent-iq
description: Develop, evaluate, package, or operate Agent IQ dynamic A2A agent discovery and orchestration.
license: MIT
---

# Agent IQ engineering skill

Agent IQ turns a user request into the smallest trusted agent squad, invokes the
selected agents through native A2A, and returns a grounded result with a
replayable lifecycle.

Read `AGENTS.md`, then inspect the source and tests for the requested change.
Use `.github/skills/deploy-agent-iq/SKILL.md` for Azure or Cowork deployment.

## Product invariant

The runtime formation is derived from:

- user intent and decomposed work items;
- request-time authorization and policy eligibility;
- catalog, Agent Card, skill, health, and compatibility signals; and
- evaluated outcomes and explicitly scoped preferences.

Atomic skills call the generic orchestration path. Never create a composite
scenario skill, fixed agent pair, or ID-specific route to choose the squad.
Selection may produce one or many agents; the planner owns dependencies and
parallelism.

Foundry is one inventory and runtime provider, not the product control plane.
Catalog, Match, Orchestrate, Trust, Runtime, Evidence, and presentation
contracts must remain compatible with other A2A providers, Agent 365, Copilot
Studio, Declarative Agents, Autopilot agents, MCP servers, and skills.

## Trust and evidence

- Security-trim before retrieval and rank only eligible candidates.
- Reauthorize immediately before invocation or consequential action.
- Use native A2A for cloud agent calls; never hide an in-process fallback.
- Treat model prose as untrusted text, not structured evidence.
- Redact public evidence before hashing and publishing it.
- Keep malformed or unavailable evidence as a local partial failure.
- Preserve claim, agent, source, invocation, caller, and policy provenance.
- Never expose hidden reasoning. Explain selection with public card, score,
  capability, policy, and lifecycle facts.
- Require prepare, explicit approval, authorized execution, verification, and
  compensation where possible for consequential writes.

## User experience

Default to a compact result:

1. meaningful formation/progress stages;
2. the selected agent or squad and concise public reason;
3. an honest answer or explicit partial/declined state; and
4. resolvable sources.

The detailed candidate set, plan, event ledger, evidence, artifacts, and
failures remain available through progressive disclosure. Support keyboard,
screen-reader, reduced-motion, narrow-screen, and historical-chat replay.

## Validation

Run focused tests while editing. Before release:

```powershell
pwsh .\scripts\verify.ps1
```

Keep discovery, orchestration, and tool-selection quality at 1.000. Cloud
changes are candidate-first, content-addressed, independently probed, promoted
only after success, and reversible to the retained prior version.
