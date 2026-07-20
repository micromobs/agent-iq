# ADR 0002: Native A2A only on the release path

**Status:** Accepted

## Decision

An agent selected for the first release must be invoked through its published,
authenticated native A2A endpoint. Missing cards, unsupported protocol,
authorization failure, timeout, or transport failure are explicit errors.

## Consequences

- Development fakes remain test adapters, never release fallbacks.
- Telemetry records the negotiated card, protocol, endpoint identity, and
  transport status.
- A release cannot pass by silently invoking an in-process graph.
