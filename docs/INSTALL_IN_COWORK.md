# Install Agent IQ in Microsoft Cowork

Agent IQ is installed as a Microsoft 365 app package containing atomic workflow
skills and one remote MCP connector for Microsoft 365 Copilot Cowork. The
connector discovers its tools at runtime.

## Before installation

Complete the Azure deployment and require all live probes to pass:

```powershell
pwsh .\scripts\agent-iq.ps1 status
pwsh .\scripts\agent-iq.ps1 test
```

The generated package is:

```text
%LOCALAPPDATA%\AgentIQ\environments\dev\agent-iq.zip
```

Do not use a ZIP copied from another tenant. The deployment command generates a
stable application ID and connector URL for the selected environment.

## Personal developer installation

With Microsoft 365 Agents Toolkit CLI authenticated:

```powershell
atk auth list
atk install --file-path `
  "$env:LOCALAPPDATA\AgentIQ\environments\dev\agent-iq.zip" `
  --scope Personal
```

Open Cowork, start a fresh conversation, and select Agent IQ.

If an updated package keeps the same application ID, increment its version:

```powershell
pwsh .\scripts\agent-iq.ps1 package `
  -AllowAnonymousDemoConnector `
  -PluginVersion 1.0.1
atk install --file-path `
  "$env:LOCALAPPDATA\AgentIQ\environments\dev\agent-iq.zip" `
  --scope Personal
```

Cowork may cache package metadata. Test an update in a fresh conversation after
the client refreshes.

## Organization installation

An authorized Microsoft 365 administrator can upload the generated ZIP as a
custom app and assign it to a test group. Use a narrow test group before any
broader assignment.

Exact Microsoft 365 admin controls, licensing, and custom-app policies vary by
tenant. Follow your tenant's approved app-review and deployment process.

## Authentication boundary

The current developer package uses:

```json
{"authorization":{"type":"None"}}
```

That configuration is intentionally limited to an isolated personal/demo
environment with synthetic data. HTTPS does not make an anonymous connector
appropriate for production.

The package builder can emit OAuth/API-key vault, Dynamic Client Registration,
or Azure Key Vault manifest shapes, but the current Agent IQ server has not
been certified to enforce those production authentication modes end to end.
Do not claim production OAuth readiness until that gate passes.

## How skills route

Agent IQ packages atomic skills such as IT, Expense, HR, Payroll, Vacation, and
product operations. A skill never fixes the squad. It calls the generic
`orchestrate` tool once; runtime intent decomposition and trusted discovery
select the actual agent or agents.

For example:

```text
My VPN is down and I need to check the status of an expense reimbursement.
```

This may form an IT plus Expense squad because the request contains two
independent work items. There is no authored "VPN and expense" skill.

## Historical conversations

The MCP resources keep current and legacy widget aliases so a reopened
conversation can resolve its saved resource URI. If a historical card shows
Retry:

1. run `pwsh .\scripts\agent-iq.ps1 status`;
2. run `pwsh .\scripts\agent-iq.ps1 test`;
3. confirm the installed app version is current; and
4. retest in both a fresh and the original conversation.

Retry cannot repair an unavailable or incompatible backend resource by itself.

## Uninstall

For a personal package, use the Title ID returned by `atk install`:

```powershell
atk uninstall -i false --mode title-id --title-id <title-id>
```

Uninstall the Cowork package before destroying its Azure environment so users
do not retain a connector that points to a removed endpoint.
