# GitHub Copilot Canvas

The Agent IQ Command Center is a thin GitHub Copilot Canvas extension over the
reusable React client in `apps/canvas`. It consumes only the governed AG-UI
projection; it cannot read the canonical lifecycle ledger, prompts, tool
arguments, caller identity, context IDs, provider errors, or source bodies.

## Layout

```text
apps/canvas/                                  React/TypeScript source and tests
.github/extensions/agent-iq-canvas/
  extension.mjs                              Copilot Canvas declaration/actions
  runtime.mjs                                loopback server and live projection client
  web/                                       committed deterministic production build
```

The extension declares `agent-iq-command-center` and three bounded actions:
`load_demo`, `load_execution`, and `set_replay_cursor`. The default open state
uses the deterministic five-agent replay. Live mode fetches
`GET /v1/executions/{execution_id}/ag-ui` server-side.

## Build and verify

Run from the repository root:

```powershell
uv run python .\scripts\generate_canvas_demo.py
Push-Location .\apps\canvas
npm ci --no-audit --no-fund
npm audit --audit-level=low
npm run typecheck
npm test
npm run build
npm run build:check
Pop-Location

node --check .\.github\extensions\agent-iq-canvas\extension.mjs
node --check .\.github\extensions\agent-iq-canvas\runtime.mjs
```

`npm run build` writes directly to the project extension's `web` directory.
`build:check` rebuilds into a temporary directory and requires byte-for-byte
equality with the committed bundle.

`npm test` runs the Vitest reducer/component suite, the loopback runtime security
suite, and eight Playwright checks in the system Microsoft Edge browser. The
browser gate runs axe-core WCAG A/AA scans in dark and light modes, verifies
keyboard-only replay, proves reduced-motion replay is explicit step navigation
rather than autoplay, and locks semantic layout signatures for 1440px desktop,
900px compact desktop, 520px Copilot right panel, and 320px reflow. It also
loads the self-contained MCP formation fallback at its 280px minimum and
requires the same WCAG and no-overflow boundary. Test artifacts are written
outside the repository.

The browser gate intentionally uses computed layout signatures rather than
pixel snapshots. This catches column, reflow, sticky-header, target-size, and
page-overflow regressions without coupling acceptance to Edge antialiasing or a
specific display scale. Microsoft Edge is part of the Windows CI image; no
browser binary is downloaded by the repository.

The complete repository gate runs the same replay, dependency, type, test,
syntax, and deterministic-build checks:

```powershell
pwsh .\scripts\verify.ps1
```

## Host registration certificate

```powershell
uv run python .\scripts\probe_copilot_canvas.py --json
```

Copilot CLI 1.0.71 prompt mode does not currently discover the project-scoped
`.github/extensions` directory. The bounded probe therefore copies this exact
extension to `~/.copilot/extensions/agent-iq-canvas`, runs one no-tool prompt,
requires the host's `session.canvas.registry_changed` event to contain the
Canvas and all three closed-schema actions, then removes only the temporary
copy. It fails rather than overwriting a different user-scoped installation.

This certificate proves extension loading and Canvas declaration acceptance. It
deliberately records `render_acceptance=not-exercised-by-prompt-mode-probe`;
actual right-panel and Present/fullscreen rendering in the GitHub Copilot
Desktop App remains a separate manual host acceptance gate.

## Live configuration

The replay needs no credentials or service. For an authorized live execution,
set these only in the process that launches the host:

```powershell
$env:AGENT_IQ_HTTP_URL = "https://<agent-iq-service>"
$env:AGENT_IQ_API_TOKEN = "<short-lived bearer token>"
```

`AGENT_IQ_HTTP_URL` must be HTTPS, except for loopback development. The optional
token remains in the extension process and is never sent to browser JavaScript.

## Security boundary

- The server binds only `127.0.0.1` and uses a random 256-bit capability token.
- Root and state APIs require the token; state mutation also requires the exact
  Origin. Hashed JavaScript and CSS assets are unauthenticated so third-party
  iframe cookie policy cannot break loading.
- Exact Host validation, traversal rejection, CSP, response-size limits, and
  recursive restricted-field rejection fail closed.
- Live polling accepts only a validated AG-UI projection and stops when the
  Canvas closes.
- The browser reducer independently accepts only snapshots and bounded
  top-level RFC 6902 deltas.
- Scrollable candidate and lifecycle regions are keyboard focusable. Focus
  indicators meet both dark and light contrast boundaries.
- Reduced-motion users step through replay events explicitly; the Canvas does
  not start timed replay in that mode. Increased-contrast and forced-color
  media queries preserve boundaries without relying on color alone.
- No optional Minecraft asset is included. Core behavior and accessibility do
  not depend on branded takeover media.

Rollback is deletion or omission of the project extension directory. It does
not change the Agent IQ service, plugin, catalog, hosted agents, or ledger.
