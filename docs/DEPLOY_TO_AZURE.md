# Deploy Agent IQ to Azure

This guide creates an isolated Agent IQ developer environment in an Azure
subscription and generates a Cowork package bound to that environment.

## What the command creates

One dedicated resource group contains:

- a Microsoft Foundry/AIServices account and project;
- GPT and embedding model deployments;
- Azure AI Search with managed identity;
- Azure Container Registry with admin credentials disabled;
- Log Analytics and a Container Apps environment;
- the Agent IQ orchestrator Container App; and
- the canonical hosted A2A agent fleet.

The command also creates one single-tenant Entra application for the
invocation-bound artifact companion. Its display name and ownership marker, and
the generated Cowork application ID, are stable for the subscription and
environment.

## Prerequisites

Install:

- PowerShell 7.4 or newer;
- Git;
- uv;
- Node.js 24;
- Azure CLI 2.80 or newer;
- Azure Developer CLI 1.27 or newer; and
- Docker Desktop with a running Linux container engine.

The deploying identity needs:

- access to an **Enabled** Azure subscription;
- permission to create resources in the target subscription;
- permission to assign Azure roles, normally **Owner** or **Contributor** plus
  **User Access Administrator**;
- permission to register required Azure resource providers; and
- permission to create an Entra application, normally the **Application
  Developer** directory role.

If directory policy prevents application creation, ask an administrator to
create a single-tenant Entra application and pass its application ID with
`-ArtifactCompanionApplicationId`.

The selected region must offer quota for `gpt-5.4-mini` and
`text-embedding-3-large`. Model availability, version, and quota vary by
subscription and region.

## Diagnose the machine

```powershell
pwsh .\scripts\agent-iq.ps1 doctor
```

After signing in to Azure, include cloud and Docker checks:

```powershell
az login --use-device-code
azd auth login --use-device-code
pwsh .\scripts\agent-iq.ps1 doctor -Cloud
```

The deploy command can initiate interactive Azure login when no current session
exists.

## Preview without changing Azure

```powershell
pwsh .\scripts\agent-iq.ps1 deploy `
  -Plan `
  -SubscriptionId <subscription-id> `
  -Environment dev `
  -Location swedencentral
```

The plan prints deterministic resource names and deployment phases. It creates
no files, identities, roles, or Azure resources.

## Deploy

> [!WARNING]
> The current cloud path is an isolated **personal demo**, not an
> organization-production deployment. It exposes an anonymous connector and
> must use synthetic data only. The command refuses to deploy unless you
> acknowledge that boundary explicitly.

```powershell
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -SubscriptionId <subscription-id>
```

Without `-SubscriptionId`, Agent IQ uses the current Azure CLI subscription.
`-AllowAnonymousDemoConnector` does not make the endpoint production-safe; it
only records explicit acceptance of the demo boundary. The command also pauses
at a cost warning. `-Yes` is intended only for a previously reviewed,
non-interactive deployment using existing authenticated sessions and never
replaces the anonymous-connector opt-in.

Deployment runs these fail-closed phases:

1. prerequisite and subscription-state checks;
2. locked dependency installation;
3. provider registration and Azure foundation provisioning;
4. managed-identity and least-privilege role assignment;
5. bootstrap orchestrator deployment;
6. canonical hosted-agent generation and deployment;
7. strict orchestrator promotion on the same immutable image;
8. tenant-specific Cowork and Copilot package builds; and
9. MCP, Vacation, consumer-product, and VPN probes.

No phase converts a cloud failure into a local or simulated success.

## Generated local state

Credential-free state is written outside Git:

```text
%LOCALAPPDATA%\AgentIQ\environments\dev\
  environment.ps1
  deployment.json
  agent-iq.zip
  cowork\
  copilot\
    agent-iq-plugin\
    agent-iq-copilot-plugin-1.0.0.zip
  reports\
    cowork\
    copilot\
```

`environment.ps1` contains resource coordinates only. It contains no passwords,
client secrets, access tokens, API keys, or registry credentials.

Use another isolated environment:

```powershell
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -Environment test
```

## Advanced options

```powershell
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -Environment test `
  -Location eastus2 `
  -ModelDeployment gpt-5.4-mini `
  -EmbeddingDeployment text-embedding-3-large-gs `
  -SearchSku Basic `
  -PluginVersion 1.0.0
```

Use an administrator-created Entra application:

```powershell
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -ArtifactCompanionApplicationId <application-id>
```

`-SkipBuild` is an advanced recovery option and requires the exact existing
`-ImageTag`. A normal deployment builds and proves a new immutable image.

## Status and verification

```powershell
pwsh .\scripts\agent-iq.ps1 status
pwsh .\scripts\agent-iq.ps1 test
```

`status` checks the public health and readiness routes. `test` exercises the
live generated environment when present.

## Cost

Azure pricing varies by region, offer, traffic, and model quota. Review the
Azure pricing pages before deployment. The main cost drivers are:

- model tokens and embedding calls;
- Azure AI Search SKU and replicas;
- Container Apps CPU, memory, and minimum replicas;
- Container Registry storage and transfer; and
- Log Analytics ingestion and retention.

Use a dedicated developer subscription or resource group, set budgets and cost
alerts, and remove idle environments. The default deployment is not a free
local emulator.

## Remove the environment

```powershell
pwsh .\scripts\agent-iq.ps1 destroy -Environment dev
```

After confirmation, Agent IQ starts deletion of the dedicated resource group.
If Agent IQ created the companion Entra application, it deletes that
application too. A supplied companion application is never deleted. Teardown
uses the subscription and resource group sealed in `deployment.json`; an
explicit subscription mismatch or environment-record mismatch is rejected
before Azure is contacted.

Container Apps, Foundry, Search, and role assignments may take several minutes
to disappear after Azure accepts the deletion.

## Troubleshooting

| Symptom | Cause and action |
| --- | --- |
| Subscription is `Disabled` | Select or reactivate an Enabled subscription. Agent IQ will not deploy to a disabled subscription. |
| Role-assignment `AuthorizationFailed` | Use Owner, or Contributor plus User Access Administrator, at the target scope. |
| Provider registration denied | Ask a subscription owner to register `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.ContainerRegistry`, `Microsoft.App`, `Microsoft.OperationalInsights`, and `Microsoft.Insights`. |
| `az cognitiveservices account project` is unavailable | Upgrade to Azure CLI 2.80 or newer and rerun `doctor -Cloud`. |
| Model deployment unavailable | Choose a supported region/model or request quota. The command will not silently substitute a model. |
| Entra app creation denied | Supply an administrator-created single-tenant application ID. |
| `azd` command missing or too old | Install Azure Developer CLI 1.27+ and rerun `doctor -Cloud`. |
| Docker daemon unavailable | Start Docker Desktop and verify `docker version` reports a server. |
| ACR push returns 403 | Confirm the subscription is Enabled and the deployer has `AcrPush`; do not enable ACR admin credentials. |
| Cowork probe fails after deployment | Run `status`, then `test`. Do not install the ZIP until all probes pass. |
| Region blocked by policy | Use an organization-approved region and rerun `-Plan` before deployment. |

Corporate Conditional Access can block device-code or headless logins. Use an
approved interactive session or ask the tenant administrator for the supported
authentication path; never work around policy by storing a secret in the
repository.
