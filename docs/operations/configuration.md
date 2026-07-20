# Configuration

Agent IQ settings use the `AGENT_IQ_` environment prefix. Configuration is
loaded only when an application calls `get_settings()`.

## Safety boundaries

| Setting | Local default | Non-local rule |
|---|---:|---|
| `AGENT_IQ_ENVIRONMENT` | `local` | `canary` or `production` |
| `AGENT_IQ_NATIVE_A2A_REQUIRED` | `true` | Must remain `true` |
| `AGENT_IQ_ALLOW_ANONYMOUS_CONNECTOR` | `false` | Forbidden in production |
| `AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT` | empty | Required |

The API key field accepts `AGENT_IQ_AZURE_API_KEY` or `AZURE_API_KEY` only from
the process environment. It is excluded from representation, diagnostic output,
and model serialization. Prefer Entra and managed identity.

## Diagnostics

Use `Settings.diagnostic_view()` for safe health/support output. Do not log
`model_dump()` of third-party configuration objects and do not log environment
variables.

## Local setup

Copy `.env.example` only for non-secret developer coordinates. `.env` is ignored
by Git. Set short-lived credentials directly in the process that needs them.

Dot-source `scripts\dev_env.ps1` before direct `uv` commands. It sets
`UV_PROJECT_ENVIRONMENT` to a generated directory under LocalAppData so package
refreshes are not blocked by OneDrive file synchronization.
