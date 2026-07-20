# Security

## Reporting

Do not open a public issue containing credentials, tenant data, private source
content, or an exploitable vulnerability. Use GitHub's
[private vulnerability report](https://github.com/micromobs/agent-iq/security/advisories/new)
so maintainers can respond without exposing the report.

## Repository policy

- No API keys, client secrets, access tokens, private keys, or connection
  strings in source, history, fixtures, screenshots, logs, or generated output.
- Use Entra identity and managed identity by default.
- Treat catalog metadata as untrusted input.
- Apply authorization before matching and again immediately before invocation.
- Do not expose hidden model reasoning.
- Fail closed on missing identity, policy, card, protocol, evidence, or scope.

Run `pwsh .\scripts\verify.ps1` before every change. It includes repository
policy and secret checks.

## Preview security boundary

Agent IQ is an early preview. The design is acceptable only for an isolated,
personal, synthetic-data demo in a subscription and tenant you own — it is
not production-ready and is not enterprise-ready. The following are known,
code-verified boundaries, not accidents:

- **Self-asserted caller identity.** The caller's principal and tenant are
  read from request headers, message metadata, or a fixed connector identity,
  not derived from an authenticated, validated token.
- **Execution access bound only to execution ID.** Reading, streaming (SSE),
  or cancelling an execution requires only its execution ID; there is no
  per-caller or per-tenant authorization check on those operations.
- **No rate limiting on the anonymous demo ingress.** The anonymous demo
  connector accepts requests without throttling or quota enforcement.
- **Unbounded in-memory execution retention.** Execution history accumulates
  in process memory with no cap or eviction policy.
- **No live enterprise connectors or production certification.** Only
  labelled synthetic fixtures ship; there is no certified production
  authentication or live enterprise-connector integration.

The one enforced safety gate: deployment configuration refuses to start with
an anonymous connector when the environment is `PRODUCTION`. That refusal is
real and tested, but it is a floor, not a substitute for the production
requirements below.

Before any production use, the deployment needs, at minimum: authenticated
principal/tenant identity derived from a validated token (not headers,
metadata, or a hard-coded constant); per-caller and per-tenant authorization
on execution read/stream/cancel; request quotas and rate limiting on
ingress; bounded, lifecycle-managed retention; and audit plus live
enterprise-connector certification. See the README
[Preview limitations](README.md#preview-limitations) for the same boundary in
user-facing terms.
