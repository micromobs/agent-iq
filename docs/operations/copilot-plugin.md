# GitHub Copilot App plugin

The Agent IQ GitHub Copilot package is a thin host adapter over the same
protocol-neutral service used by Cowork, HTTP/SSE, and incoming A2A. It contains
one custom agent, two versioned skills, and one remote MCP registration. Match,
policy, planning, execution, evidence, and lifecycle behavior remain in the
Agent IQ service.

## Package contract

```text
packages/copilot/
  plugin.json
  .mcp.json
  agents/agent-iq.agent.md
  skills/orchestrate-agents/SKILL.md
  skills/replay-execution/SKILL.md
```

The custom agent allowlists the six bounded Agent IQ operations and optional
read-only `workiq/ask`. The orchestration skill may use search, orchestration,
capabilities, and `workiq/ask`; replay may use only execution lookup and
capabilities. Business mutation, generic Work IQ writes, hidden reasoning,
provider errors, tool arguments, and unrestricted MCP wildcards are forbidden.

The generated package targets an anonymous non-production environment. The
repository `.mcp.json` contains an intentionally nonfunctional endpoint
template and must not be loaded directly. The plugin does not claim preference
memory, production identity, or durable execution that the service has not
recorded. The rich Canvas is a separate project extension documented in
[`copilot-canvas.md`](copilot-canvas.md); it is not embedded in the plugin ZIP.

## Build

The root lifecycle command generates an endpoint-bound plugin beside the Cowork
package:

```powershell
pwsh .\scripts\agent-iq.ps1 package `
  -AllowAnonymousDemoConnector `
  -Environment dev
```

For another deployment pipeline, provide the exact endpoint explicitly:

```powershell
uv run python .\deployments\copilot\build.py `
  --mcp-url https://<your-agent-iq-host>/mcp `
  --json
```

The build validates the source template and configured output: exact file set,
frontmatter, version parity, HTTPS MCP endpoint, tool allowlists, redaction
language, and absence of hard-coded agent routes. It then emits a
byte-deterministic ZIP and matching directory with fixed timestamps and file
modes. A normal team deployment writes:

```text
%LOCALAPPDATA%\AgentIQ\environments\dev\copilot\agent-iq-plugin\
%LOCALAPPDATA%\AgentIQ\environments\dev\copilot\agent-iq-copilot-plugin-1.0.0.zip
```

Build reports record only names, versions, paths, file lists, and hashes. They
contain no endpoint, prompt, answer, source body, credential, or tenant data.

## Load and verify

Load only the generated directory:

```powershell
copilot `
  --plugin-dir "$env:LOCALAPPDATA\AgentIQ\environments\dev\copilot\agent-iq-plugin" `
  --agent agent-iq:agent-iq
```

The host probe starts a non-interactive session and requires
`orchestrate-agents` plus `replay-execution` from the plugin, one connected
`agent-iq` HTTP MCP server sourced from the plugin, the namespaced custom agent,
a successful terminal event, and zero tool calls. It persists only this bounded
summary under `reports/evals/copilot-plugin`.

Copilot CLI 1.0.71 recognizes the local custom agent as
`agent-iq:agent-iq`. Its standalone `skill list` and `mcp list` subcommands do
not currently apply `--plugin-dir`, despite the global option, so those commands
are not acceptance gates. Session events from the namespaced agent are the
certified source of truth.

The optional marketplace Work IQ plugin is installed independently:

```powershell
copilot plugin install workiq@copilot-plugins
```

Agent IQ references only `workiq/ask` and passes selected context references,
not Microsoft 365 source bodies, to orchestration.

## Inline MCP App

`search_agents` and `orchestrate` attach the self-contained
`ui://agent-iq/formation.html` MCP App. The view follows the stable MCP Apps
2026-01-26 contract and renders only states the host can prove: partial or
complete tool input, terminal result, cancellation, and host-context changes.
It does not manufacture intermediate server progress. A search result can show
the selected formation for review; an orchestration result can prove routing,
approved evidence, completion, partial outcome, safe decline, or failure.

The view requests no network, frame, or static-resource origins, draws its own
boundary, and stays below 64 KiB. It supports inline and fullscreen modes only
when the host advertises them, returns to inline with Escape, and retains plain
structured/text output when the host does not support MCP Apps.

Inline sizing is intentionally one-way: the app sends one bounded height update
after initialization or a rendered state change and never observes the host
iframe for continuous resizing. This prevents host/app resize feedback loops and
keeps the compact view at a stable height with internal scrolling when needed.

## Safety and rollback

- Never promote the current anonymous endpoint as a production integration.
- Do not add `*`, broad Work IQ tools, agent-specific routes, or host-specific
  orchestration instructions.
- Do not expose canonical event payloads directly. The Canvas consumes only the
  governed AG-UI projection.
- Local rollback is immediate: omit `--plugin-dir`.
- Removing the generated plugin directory does not mutate the Agent IQ service,
  catalog, hosted agents, Cowork package, or execution ledger.

The GitHub Copilot App repository is proprietary and was reviewed only to
confirm that it does not publish extension source. Package contracts were
derived from public GitHub plugin examples, documentation, and Microsoft's
public Work IQ plugin at the pinned revisions in
`docs/provenance/sources.json`; no proprietary host source was copied.
