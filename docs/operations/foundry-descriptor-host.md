# Descriptor-driven Foundry host

The descriptor host replaces per-agent source copies with one immutable image
per runtime family. Each Foundry Agent object still has its own Entra identity,
card, version, toolbox binding, and rollback record. The first canary maps
`consumer-formulation-rd-host-canary` to the canonical logical agent
`consumer-formulation-rd`; it does not change the routed synthetic consumer
agent.

## Required configuration

Set these non-secret values or pass their matching script parameters:

- `AGENT_IQ_AZURE_RESOURCE_GROUP`
- `AGENT_IQ_AZURE_AI_ACCOUNT`
- `AGENT_IQ_AZURE_AI_PROJECT`
- `AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT`
- `AGENT_IQ_AZURE_SEARCH_RESOURCE_GROUP`
- `AGENT_IQ_SEARCH_SERVICE`
- `AGENT_IQ_AZURE_ACR_RESOURCE_GROUP`
- `AGENT_IQ_AZURE_CONTAINER_REGISTRY`
- `AGENT_IQ_MUTATION_LEASE_RESOURCE_GROUP`
- `AGENT_IQ_MUTATION_LEASE_STORAGE_ACCOUNT`
- `AGENT_IQ_MUTATION_LEASE_CONTAINER`

Authentication comes from `scripts\foundry_login.ps1`. Do not put credentials
in the image, plan, deployment record, or command line.

Foundry reserves every `AGENT_*` and `FOUNDRY_*` container variable. Host-private
integrity selectors therefore use `AIQ_HOST_*`; platform-supported settings use
`AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `TOOLBOX_NAME`,
and `TOOLBOX_ENDPOINT`. Plan validation rejects reserved prefixes before any
deployment request.

The deployment script validates every resource group, Search, ACR, storage, and
Foundry coordinate before authentication. After authentication it verifies the
exact ARM resource IDs. It never creates or changes shared connections, storage,
or RBAC. Pre-provision `Search Index Data Reader` for the project identity and
the applicable ACR pull role: `AcrPull` for legacy RBAC or `Container Registry
Repository Reader` for ABAC.

## Exclusive mutation lease

An infrastructure operator must pre-provision one zero-content block blob at:

```text
https://<storage>.blob.core.windows.net/<container>/agent-iq/foundry/<account>/<project>/<deployment-agent>.lock
```

Grant the deployment identity `Storage Blob Data Contributor` at the narrowest
usable scope. The host command acquires an infinite lease before its first live
Foundry state read, verifies ownership around every mutation, writes sealed
operation evidence while still holding the lease, and then releases it. The
deployment record stores only the SHA-256 binding of this canonical URL.

The lease serializes Agent IQ operators that use this release path. Foundry does
not expose an agent/version ETag or conditional mutation contract, so this lease
cannot fence an administrator who bypasses Agent IQ through the portal or direct
API. Exact traffic, inventory, and ownership checks still fail closed on such
drift.

If a process exits without releasing its infinite lease, first prove that no
Agent IQ deployment or rollback is active, then break it explicitly:

```powershell
az storage blob lease break --auth-mode login `
  --account-name <storage> --container-name <container> `
  --blob-name agent-iq/foundry/<account>/<project>/<deployment-agent>.lock
```

## Toolbox identity compatibility

As of 2026-07-12, the Foundry Toolbox MCP path does not forward the agent
blueprint and instance identity context required to resolve an
`AgenticIdentityToken` project connection. The live service returns
`HTTP_400` before `tools/list`, even though the hosted agent has an instance
identity. This is a platform capability gap, not an authorization fallback in
Agent IQ.

The isolated canary therefore defaults its pre-existing Foundry IQ connection
to the documented `project-managed-identity` authentication mode. The script
verifies its exact target and auth mode without changing either. Use
`-GroundingAuthType agentic-identity` only as a capability probe; it remains
fail-closed until the Toolbox path propagates the agent identity. Per-agent
grounding identity remains a blocker for fleet migration and GA readiness.

## Deploy and certify

From the repository root:

```powershell
pwsh scripts\deploy_foundry_host_canary.ps1 `
  -MutationLeaseStorageAccount <storage> `
  -MutationLeaseContainer <container>
```

The script performs these fail-closed gates:

1. validates all target coordinates before login, then verifies the model,
   Search service, project identity, ACR, pre-provisioned lease blob, existing
   logical-agent toolbox, and exact Foundry IQ connection target;
2. builds and pushes a linux/amd64 image, resolves its registry digest, and
   deploys only the immutable `@sha256:` reference;
3. runs the image's credential-free `--check`, which verifies the logical
   agent ID, runtime family, toolbox, and canonical descriptor fingerprint;
4. acquires the exclusive Blob lease, creates a new Foundry hosted-agent
   version using Responses `2.0.0`, waits for `active`, and explicitly enables
   Entra-authenticated incoming A2A;
5. certifies incremental Responses events, A2A v1.0 JSON-RPC, canonical card
   parity, exact image and descriptor metadata, grounding, and citations.

Evidence is written under
`reports\deployments\foundry\descriptor-host-canary\latest.json`.

Exact citation titles are not left to model formatting. After the model's final
turn, the LangGraph host copies any missing source-title/reference pairs from
the actual governed tool messages into a deterministic Sources block. It does
not invent, rename, or resolve a source from answer prose.

Foundry incoming A2A is a text-only preview and does not provide SSE. The
Responses probe certifies incremental streaming independently; A2A uses
`message/send`.

The certified canary is version `3`, pinned to image digest
`sha256:5dde75c451b923c9689db64c367ce105c7a0d7208e5663342149fdebd51d34fd`.
Both the deployment run and a later `-ProbeOnly` replay passed.

## Reprobe and rollback

```powershell
pwsh scripts\deploy_foundry_host_canary.ps1 -ProbeOnly `
  -MutationLeaseStorageAccount <storage> -MutationLeaseContainer <container>

pwsh scripts\deploy_foundry_host_canary.ps1 -Rollback `
  -RollbackRecord <run>\<operation-id>.deployment.json `
  -MutationLeaseStorageAccount <storage> -MutationLeaseContainer <container>
```

Rollback never deletes an Agent object or version. For an existing Agent it
restores the exact prior traffic selector and retains the operation-created
immutable version. For a newly created isolated canary it records
`status=retained` and leaves that version available for diagnosis. A failed
deployment similarly performs no compensating mutation because the cloud state
may be uncertain. The explicit, SHA-256-sealed operation record and the same
lease-resource binding are mandatory for rollback.
