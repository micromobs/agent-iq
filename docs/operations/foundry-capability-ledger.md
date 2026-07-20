# Foundry capability ledger

Agent IQ probes the live Microsoft Foundry runtime before enabling a platform
feature. The ledger keeps documented availability separate from observed tenant
availability and performs no cloud mutation.

## Run

```powershell
cd C:\path\to\agent-iq
$env:AGENT_IQ_AZURE_AI_ACCOUNT = "<trusted-account-name>"
$env:AGENT_IQ_AZURE_AI_PROJECT = "<trusted-project-name>"
.\scripts\probe_foundry_capabilities.ps1
```

The operator logs in with the existing headless Foundry credential. The probe
requires independently supplied account and project names and rejects an endpoint
that does not match them before constructing credentials or HTTP clients. It writes:

```text
reports\deployments\foundry\capabilities\<timestamp>\capabilities.json
reports\deployments\foundry\capabilities\latest.json
```

The report covers the account region and network posture, deployed model/SKU
shapes, the ARM model catalog, GPT-5.6 Sol/Luna/Terra capacity and quota,
Global/Data Zone/Regional placement, hosted agents and sessions, Toolboxes,
Memory, Routines, Schedules, Skills, model operations, the active canary's
Responses and A2A protocols, and capabilities that require a mutating
certification run.

## Result taxonomy

| Result | Meaning |
|---|---|
| `available` | The API or certified behavior was observed live. |
| `unavailable` | The endpoint or offer is not exposed in this scope. |
| `unauthorized` | The caller cannot perform the read-only probe. |
| `not_configured` | The capability exists but this project has no configured instance. |
| `mutation_required` | A safe read cannot prove the behavior; use an isolated canary. |
| `probe_error` | The probe itself failed and must not be treated as absence. |

Invocations, Activity, background cancellation, WebSocket, and Microsoft 365
publishing remain `mutation_required` until their separate isolated canaries
create the state needed to test them.

## Content safety

The persisted report uses allowlisted facts only. It never stores tenant,
subscription, resource, project, account, deployment, agent, or endpoint names;
prompts; request or response payloads; model answers; tokens; or raw exception
messages. Model offer names, SKU names, lifecycle states, capacity, and quota are
retained because they are the capability under test.

The protocol observations consume only the status, transport, timestamp, wire
version, and fallback facts from the descriptor-host certificate. They do not
copy its prompt, answer, card, endpoint, identity, or resource coordinates.
