# Foundry hosted-agent generation

Agent IQ generates hosted-agent projects from canonical descriptors. Agent IDs,
cards, skills, model settings, knowledge bindings, runtime family, and artifact
capabilities are data-driven; the generator contains no agent-specific routing
branches.

## Generate and validate offline

```powershell
pwsh .\scripts\generate_consumer_deployments.ps1
```

Generated scratch output is written under `%LOCALAPPDATA%\AgentIQ` rather than
the source tree. Every project is self-contained and validated for:

- descriptor and Agent Card parity;
- A2A and Responses protocol declarations;
- import closure and pinned runtime dependencies;
- managed-identity configuration;
- required Foundry IQ knowledge bindings; and
- forbidden secrets, tenant coordinates, and local source paths.

Generate only selected agents:

```powershell
pwsh .\scripts\generate_consumer_deployments.ps1 `
  -Only employee-it-helpdesk,employee-expense-finance
```

## Deploy

The normal path is the root deployment command:

```powershell
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -SubscriptionId <subscription-id>
```

It loads generated coordinates from the local environment record and deploys
the complete canonical fleet.

Advanced operators can invoke `scripts\deploy_consumer_fleet.ps1` directly
after loading:

```powershell
. "$env:LOCALAPPDATA\AgentIQ\environments\dev\environment.ps1"
```

Pass all target coordinates explicitly or use the generated `AGENT_IQ_*`
environment variables. Never copy coordinates from another tenant.

## Rollback and evidence

The deployer records exact previous and created versions, immutable image
digest, traffic state, identity, role assignment, and target binding. Rollback
requires the sealed, run-specific operation record. It refuses mutable image
references, changed version inventories, drifted traffic, or a record for
another project.

Each rollout stops at the first failed card, native-A2A, grounding, evidence, or
quality gate. A failed cloud call is never replaced by an in-process success.
