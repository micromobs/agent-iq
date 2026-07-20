# Demo operations

Use the credential-free showcase:

```powershell
pwsh .\scripts\agent-iq.ps1 demo
```

Use the supported live environment only after:

```powershell
pwsh .\scripts\agent-iq.ps1 status
pwsh .\scripts\agent-iq.ps1 test
```

The exact user prompts, expected squads, source boundaries, and negative checks
are maintained in `evals\launch_command_center.json`, the sole source of truth
for this scripted flow. Deployment and rollback are maintained in
[Deploy to Azure](../DEPLOY_TO_AZURE.md).
