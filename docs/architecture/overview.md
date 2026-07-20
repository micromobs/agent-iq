# Agent IQ architecture

Agent IQ separates product responsibilities into six public capabilities:
Catalog, Match, Orchestrate, Runtime, Trust, and Canvas.

```text
Hosts
  -> Agent IQ API (MCP, HTTP, SSE)
  -> Trust prefilter
  -> Catalog and Match
  -> Orchestrate execution DAG
  -> Runtime adapters
  -> Evidence and synthesis
  -> Events, traces, feedback, and outcomes
```

## Boundaries

- **Catalog** projects authoritative external registries into canonical,
  versioned descriptors.
- **Match** filters eligibility before retrieval, then ranks and explains from
  public card evidence.
- **Orchestrate** converts intent into an explicit dependency graph.
- **Runtime** negotiates cards and invokes protocols without leaking
  framework-specific behavior into the planner.
- **Trust** carries identity and policy through every stage and re-authorizes
  immediately before action.
- **Canvas** consumes stable lifecycle events and never depends on hidden model
  reasoning.

Microsoft services provide identity, registries, transports, knowledge, and
governance controls. Agent IQ adds the cross-platform catalog projection,
intent-to-agent matching, planning, execution, evidence merge, feedback loop,
and host-independent experience.

## Dynamic composition invariant

Product runtime behavior is driven by canonical descriptors, source adapters,
capability metadata, policy, and request context. Agent IQ must not require a
code change to onboard another agent, MCP server, skill, knowledge source, or
context provider.

- Agent and resource IDs are data, never routing branches or source-code
  allowlists.
- Catalog adapters enumerate and reconcile all authorized resources using
  pagination, incremental refresh, and versioned descriptors.
- Match and Orchestrate operate on the eligible descriptor set, independent of
  provider, framework, tenant, or demo fleet.
- Runtime selects a registered adapter from negotiated capabilities; it never
  maps a known agent ID to handwritten invocation code.
- Caller identity and context enter on every request and are security-trimmed
  before retrieval, minimized before invocation, and re-authorized at action
  time.
- Fan-out, candidate-window, deadline, and concurrency limits are policy or
  configuration controls, not catalog-size assumptions.

The 20 Fabrikam Consumer Products (synthetic demo) agents and five deep demo
workflows are certification fixtures and product seed data only.
Scenario-specific defaults, exact-count checks, and hero IDs belong in
deployment or evaluation modules and cannot affect the generic Catalog, Match,
Orchestrate, Runtime, Trust, or Evidence paths.

## First vertical slice

The first release path is:

```text
Cowork
  -> Agent IQ remote MCP
  -> 20 published synthetic consumer Agent Cards
  -> Match
  -> one or more native Foundry A2A calls
  -> structured evidence
  -> grounded synthesis and citations
```

The working legacy deployment stays isolated until the canary passes its release
gates.
