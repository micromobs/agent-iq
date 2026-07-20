# Agent IQ Employee Operations — SharePoint (SPFx) surface

An **optional** SharePoint Framework (SPFx) Copilot component that renders the Agent IQ
Employee Operations experience natively inside Microsoft 365 Copilot / Viva Connections.
It packages **30 fictional employee-operations scenarios** (the same synthetic catalog the
`apps/canvas` demos draw on — including the vacation-days "Ring dial") as a self-contained,
disconnected preview.

> **This is a demonstration surface, not a product integration.** It is an alternate host for
> the synthetic Agent IQ demo, letting M365 organizations see the experience rendered inside
> their own tenant chrome without wiring up any backend.

## Disconnected-by-design boundary

This solution ships **only a packaged local catalog** of fictional scenarios. It does **not**:

- Connect to Agent IQ services, Azure resources, or any hosted runtime.
- Read from Microsoft Graph, SharePoint lists/sites, or any tenant data source.
- Read the signed-in user's profile — the component renders a fixed synthetic `"Employee"`.
- Write to, submit to, or mutate any system of record.

The Copilot instruction file (`copilot/instruction.txt`) enforces the same boundary at the
model layer: every rendered outcome is described as a synthetic, read-only draft.

## Relationship to the rest of the repo

| Surface | Location | What it is |
| --- | --- | --- |
| Canvas demo | `apps/canvas` | Web widgets (Ring dial, VPN-recovery, …), validated in CI |
| Cowork connector | `packages/cowork` | Microsoft 365 Cowork remote-MCP packaging |
| Copilot plugin | `packages/copilot` | Declarative agent + plugin packaging |
| **SharePoint surface** | **`packages/sharepoint`** | **This SPFx Copilot component (optional, adopter-built)** |

## CI / supply-chain note

This SPFx solution is **excluded from the repository's continuous-integration supply-chain
gate**. The core CI (`scripts/verify.ps1`) only installs and vulnerability-scans the
`apps/canvas` dependency graph and the Python lockfile. SPFx pulls a very large beta
dependency tree that adopters install locally in their own environment, so:

- No `package-lock.json` is committed here (run `npm install` yourself).
- Its dependencies are **not** covered by the repo's OSV gate.

Treat this as an adopter-owned build you provision inside your own M365 tenant.

## Prerequisites

- Node.js **>= 22.14.0 < 23** (SPFx 1.24 requirement — see `package.json` `engines`).
- A Microsoft 365 tenant with a SharePoint **App Catalog** and Copilot component support.
  A free [Microsoft 365 developer tenant](https://aka.ms/o365devprogram) works well.
- Tenant admin (or app-catalog admin) rights to upload and approve the package.

## Build

```bash
cd packages/sharepoint/agent-iq-employee-operations
npm install -g @rushstack/heft   # one-time, if heft is not on PATH
npm install
npm run build                    # heft test --production && heft package-solution --production
```

The signed package is written to `solution/*.sppkg` (git-ignored).

## Local preview

```bash
npm start   # heft start — serves against the hosted Copilot workbench
```

Then open `https://<your-tenant>/_layouts/CopilotWorkbench.aspx` (see `config/serve.json`,
which uses a `{tenantDomain}` placeholder) and add the Employee Operations component.

## Deploy to your tenant

1. Upload the generated `solution/*.sppkg` to your tenant **App Catalog**.
2. Approve the requested permissions (the manifest requests `identity` only).
3. Add the **Agent IQ Employee Operations** declarative agent / component to a Copilot or
   Viva Connections surface.
4. Ask one of the packaged conversation starters (see `copilot/declarativeAgent.json`).

## Identifiers

The GUIDs in `config/package-solution.json`, `copilot/manifest.json`, and the component
manifest are SPFx **package/component identity** (like package names) — not tenant,
subscription, or secret values. Regenerate them if you fork this into your own solution.

## Layout

```
config/       SPFx build + packaging config (placeholders only — no tenant/keys)
copilot/      Declarative agent, plugin, and instruction files (synthetic boundary)
src/          The EmployeeOperations Copilot component + synthetic demo data
```

## Disclaimer

**THIS CODE IS PROVIDED _AS IS_ WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESS OR IMPLIED,
INCLUDING ANY IMPLIED WARRANTIES OF FITNESS FOR A PARTICULAR PURPOSE, MERCHANTABILITY, OR
NON-INFRINGEMENT.**
