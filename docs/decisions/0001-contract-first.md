# ADR 0001: Contract-first architecture

**Status:** Accepted

## Decision

Define versioned canonical contracts for catalog entries, matches, plans,
events, evidence, tasks, and errors before implementing product behavior.
Publish language-neutral JSON Schemas and keep framework-specific models behind
adapters.

## Consequences

- Cowork, Canvas, HTTP, and MCP consume the same semantics.
- Adapters can change without rewriting Match or Orchestrate.
- Breaking changes require an explicit contract version and migration.
