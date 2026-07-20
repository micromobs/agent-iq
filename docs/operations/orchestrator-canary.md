# Orchestrator canary operations

The supported entry point is:

```powershell
pwsh .\scripts\agent-iq.ps1 deploy -Plan -SubscriptionId <subscription-id>
pwsh .\scripts\agent-iq.ps1 deploy `
  -AllowAnonymousDemoConnector `
  -SubscriptionId <subscription-id>
```

The root command provisions the foundation, deploys a zero-traffic candidate,
probes its private revision FQDN, promotes it only after success, and retains the
previous serving revision for rollback.

Generated coordinates are loaded from:

```text
%LOCALAPPDATA%\AgentIQ\environments\<environment>\environment.ps1
```

Use:

```powershell
pwsh .\scripts\agent-iq.ps1 status
pwsh .\scripts\agent-iq.ps1 test
```

Advanced operators may call `scripts\deploy_orchestrator_canary.ps1` directly
only when every required coordinate is passed explicitly. Never copy a resource
group, tenant ID, application ID, Search service, ACR, or endpoint from another
environment.

The deployment remains fail-closed:

- the image is proven by immutable digest;
- candidate and public FQDNs are bound to the configured Container App;
- redirects, ambient proxies, credentials in URLs, and mutable image references
  are rejected;
- the serving revision remains at 100% until candidate probes pass; and
- failed candidates never receive public traffic.

See [Deploy to Azure](../DEPLOY_TO_AZURE.md) for the normal workflow and
teardown.
