---
name: deploy-agent-iq
description: Deploy Agent IQ and its A2A agent fleet to a developer-owned Azure subscription, build tenant-specific Cowork and Copilot packages, verify scenarios, inspect status, or remove the environment.
---

# Deploy Agent IQ

Use the repository CLI as the only deployment control plane. Do not reconstruct
Azure commands from this skill when the CLI already owns the operation.

## Safety rules

- Never request, print, persist, or paste client secrets, access tokens, API
  keys, registry passwords, or generated credentials.
- Use the developer's existing Azure CLI and Azure Developer CLI sessions.
- Run the dry-run plan before any first deployment.
- Show the cost warning and obtain explicit confirmation before using `-Yes`.
- Stop on a disabled subscription, missing role, unavailable model/region,
  quota failure, policy denial, or failed verification. Do not create a
  success-shaped fallback.
- Treat connector authorization type `None` as a personal/demo configuration,
  not a production security posture.
- Do not install the Cowork package tenant-wide. Give the generated ZIP to the
  developer or install it only at Personal scope when explicitly requested.
- Never load the source `packages\copilot` directory; its MCP URL is a
  nonfunctional template. Use only the generated environment-specific plugin.

## First deployment

From the repository root:

```powershell
pwsh .\scripts\agent-iq.ps1 doctor
pwsh .\scripts\agent-iq.ps1 deploy -Plan -SubscriptionId <subscription-id>
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -SubscriptionId <subscription-id>
```

If Azure sessions already exist, `doctor -Cloud` adds login, subscription, azd,
and Docker checks. The deploy command can initiate interactive Azure login when
needed.

The final command:

1. validates local and Azure prerequisites;
2. installs the locked project dependencies;
3. provisions the isolated Azure foundation;
4. deploys the canonical hosted A2A agent fleet;
5. deploys and promotes the Agent IQ orchestrator;
6. generates tenant-specific Cowork and Copilot packages; and
7. probes the MCP contract, Vacation, consumer-product, and VPN flows.

Generated environment state is credential-free and lives outside Git at:

```text
%LOCALAPPDATA%\AgentIQ\environments\<environment>\
```

The command prints the exact Cowork and Copilot package paths on success.

## Choices to gather

Ask only for values that cannot be inferred safely:

1. Azure subscription, if more than one enabled subscription is available.
2. Environment name, if `dev` is not appropriate.
3. Azure region, if `swedencentral` is not appropriate.
4. Explicit acceptance of billable Azure resources.

Do not ask the developer to invent resource names. The provisioner derives
stable, collision-resistant names from subscription and environment.

## Install and test in Cowork

For a personal developer install:

```powershell
atk install --file-path "<generated-agent-iq.zip>" --scope Personal
```

Then start a fresh Cowork conversation and try:

```text
My VPN is down.
How much vacation do I have remaining this year?
Which enzymes power BrightWash Cold cleaning?
```

Use a clean conversation when checking package updates because the Cowork
client may cache package metadata.

## Operate the environment

```powershell
pwsh .\scripts\agent-iq.ps1 status
pwsh .\scripts\agent-iq.ps1 test
pwsh .\scripts\agent-iq.ps1 package `
  -AllowAnonymousDemoConnector `
  -PluginVersion 1.0.1
```

`test` uses the generated environment when present; otherwise it runs the
credential-free offline evaluation suite.

## Remove the environment

```powershell
pwsh .\scripts\agent-iq.ps1 destroy
```

This starts deletion of the dedicated resource group and removes the Entra
artifact-companion application only when Agent IQ created it. It does not
remove a developer-supplied Entra application.

## Report completion

Report only:

- environment and region;
- resource group;
- MCP endpoint;
- generated Cowork ZIP path;
- verification outcome; and
- any honest preview, licensing, authentication, quota, or policy limitation.

Never include tokens, credentials, tenant content, prompts, or raw cloud error
bodies in the completion message.
