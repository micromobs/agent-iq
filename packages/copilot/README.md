# Agent IQ Copilot plugin

This non-production plugin adds one Agent IQ custom agent, two workflow skills,
and the remote Agent IQ MCP service to GitHub Copilot CLI and the GitHub Copilot
app.

## Generate a tenant-specific plugin

The repository `.mcp.json` contains a deliberately nonfunctional endpoint
template. Do not load or install this source directory directly. After deploying
an Agent IQ environment, generate the endpoint-bound plugin:

```powershell
pwsh .\scripts\agent-iq.ps1 package `
  -AllowAnonymousDemoConnector `
  -Environment dev
copilot `
  --plugin-dir "$env:LOCALAPPDATA\AgentIQ\environments\dev\copilot\agent-iq-plugin" `
  --agent agent-iq:agent-iq
```

For a custom deployment pipeline, pass its exact HTTPS MCP endpoint:

```powershell
uv run python .\deployments\copilot\build.py `
  --mcp-url https://<your-agent-iq-host>/mcp `
  --json
```

The deterministic build writes a loadable directory and matching review ZIP.
The source template is never rewritten.

The optional Microsoft Work IQ plugin is available from the default Copilot
plugin marketplace:

```powershell
copilot plugin install workiq@copilot-plugins
```

Agent IQ allows only `workiq/ask` in its agent and orchestration skill. Work IQ
is used for least-context source discovery; source bodies are never copied into
Agent IQ context references.

## Boundaries

The MCP endpoint is an anonymous canary for test users. The plugin has no
preference memory, durable async execution, business-system mutation, or custom
Canvas extension. The Canvas is delivered and certified separately.

See `docs\operations\copilot-plugin.md` for validation, use, and rollback.
