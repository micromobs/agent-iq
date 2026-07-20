# Native A2A runtime

Agent IQ Demo 1 invokes Foundry agents only through authenticated native A2A.
`NativeA2ARuntime` resolves the live catalog record, rejects inactive or
non-hosted agents, negotiates the v1.0 card before v0.3, reconciles canonical
card fields, and sends through the official A2A SDK JSON-RPC binding. There is
no local or in-process fallback.

## Invocation controls

- One deadline covers catalog resolution, authentication, card fetch, send,
  and response parsing.
- An `asyncio.Event` cancels an in-flight operation and returns the stable
  `cancelled` error code.
- A possibly accepted send is attempted once by default.
- A transient failure can be retried only when the caller explicitly selects
  `retry_with_key`, supplies an idempotency key, and sets a bounded attempt
  count. Every attempt then uses the same deterministic A2A message ID.
- Authentication, authorization, missing-agent, card, protocol, timeout,
  cancellation, and invocation failures remain distinct typed errors.
- Runtime telemetry contains IDs, protocol, attempt, duration, and error
  metadata only. It rejects prompt, answer, token, authorization, and
  idempotency-key fields.

## Live conformance

From the repository root:

```powershell
pwsh .\scripts\run_foundry_a2a_conformance.ps1
```

The gate performs one paginated inventory read, requires the exact canonical
20-agent synthetic consumer set, negotiates and reconciles all 20 cards, then
invokes `consumer-formulation-rd`. It atomically writes content-safe evidence to
`reports\evals\foundry-a2a\latest.json`.

This milestone mutates no cloud resources. Its rollback is removal of the
runtime composition; the deployed Foundry fleet is unchanged.
