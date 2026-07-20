# Agent IQ contributor guide

Agent IQ dynamically forms the smallest trusted agent squad for each request.
Intent decomposition, authorization, discovery, selection, planning, native A2A
invocation, evidence, and synthesis stay generic and catalog-driven.

## Non-negotiable architecture

- Do not add scenario-specific routes or composite skills that hard-code the
  participating agents.
- Security trimming happens before ranking and authorization is checked again
  before invocation.
- Cloud execution uses native A2A. Never hide a local or in-process fallback.
- Evidence is structured and source-bound. Failed or unavailable work stays
  explicit.
- Public explanations use card, score, policy, and lifecycle facts; never
  expose private model reasoning.
- Keep fixture data visibly labelled and never represent it as a live business
  system.

## Setup and validation

```powershell
pwsh .\scripts\agent-iq.ps1 install
pwsh .\scripts\agent-iq.ps1 test
pwsh .\scripts\verify.ps1
```

Use the locked `uv` environment. Run the smallest focused tests while editing,
then the repository verification gate before submitting a change. Discovery,
orchestration, and tool-selection quality must remain 1.000.

## Azure deployment

Use only the supported root command:

```powershell
pwsh .\scripts\agent-iq.ps1 deploy -Plan -SubscriptionId <subscription-id>
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -SubscriptionId <subscription-id>
```

Do not place subscription IDs, tenant IDs, resource names, endpoints, secrets,
or developer-specific paths in tracked files. Generated environment state
belongs under `%LOCALAPPDATA%\AgentIQ`.

## Code changes

- Preserve strict typing and published wire compatibility.
- Reuse canonical contracts, descriptors, and helpers instead of duplicating
  behavior.
- Fail closed on malformed identity, policy, transport, evidence, or
  configuration.
- Keep cloud mutations idempotent, dry-runnable, candidate-first, and
  reversible.
- Update tests and task-oriented documentation with behavior changes.
