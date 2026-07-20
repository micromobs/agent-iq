# ADR 0005: Build on Microsoft systems of record

**Status:** Accepted

## Decision

Use Microsoft platform capabilities as their intended systems of record:

- Agent 365 for supported registry and lifecycle data;
- Entra Agent ID and managed identity for identity;
- Foundry A2A, Work IQ, and MCP for transports;
- Foundry IQ, Work IQ, Fabric IQ, and Web IQ for context;
- Agent Governance Toolkit, Purview, Defender, and Agent 365 controls for
  supported governance enforcement.

Agent IQ owns the federated catalog projection, intent matching, orchestration,
runtime coordination, context-to-agent binding, evidence merge, experience, and
outcome loop.

## Consequences

- Adapters expose source authority and freshness.
- Agent IQ does not claim to replace Microsoft control planes.
- Preview or unavailable APIs have explicit degraded states, not false
  success.
