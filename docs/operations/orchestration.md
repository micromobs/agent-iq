# Dynamic orchestration

Agent IQ turns a caller-scoped intent into a validated execution DAG without
agent-specific routing code:

```text
Match -> work items -> least context -> plan -> native A2A -> evidence -> synthesis
```

The coordinator uses canonical catalog descriptors, public Match selections,
capability metadata, eligibility policy, and approved context bindings. Agent IDs
in the synthetic consumer evaluation data are certification labels, not runtime
branches. Adding an eligible native-A2A agent changes orchestration through
catalog data alone.

## Planning

- Match security-trims candidates before ranking and records the exact work item
  on every selection.
- Intent decomposition recognizes independent clauses and explicit `then`,
  `before`, and `after` ordering.
- The least-context assembler includes only approved references explicitly bound
  to the selected agent or its matched relevance labels.
- The planner rejects missing or non-native-A2A descriptors and emits an acyclic,
  dependency-validated plan.
- Selection, concurrency, per-step deadline, and overall deadline limits are
  configurable safety policy, not catalog-size assumptions.

The complete multi-intent objective is not copied into every prompt. A step sees
only its assigned work item, its approved context references, and successful
outputs from declared prerequisites.

## Execution and trust

Independent ready steps run concurrently up to the configured bound. Dependent
steps run only after every prerequisite succeeds. Immediately before each send,
Agent IQ re-reads the descriptor and re-evaluates caller eligibility.

Every delegation uses `NativeA2ARequest` through the native-only dispatcher. The
executor binds and validates agent, invocation, execution correlation, caller,
tenant, protocol, deadline, and cancellation state. A returned A2A identity or
correlation mismatch fails closed.

The runtime adds a model-visible response contract requiring agents to preserve
only inline source markers actually returned by approved tools. The original
work-item prompt remains unchanged for structured evidence retrieval. A
recoverable governed-tool no-data or invalid-input result is returned to the
agent as an explicit failed tool result so it can select another approved tool
or state the gap; authorization and policy failures still terminate the step.

A successful step requires authenticated structured evidence. Text without
evidence is discarded and represented as an honest decline. Agents advertising
typed artifacts also require a trusted producer identity and an invocation-bound
artifact grant before invocation.

## Failure semantics

| Condition | Step result | Execution behavior |
|---|---|---|
| Independent failure or timeout | `failed` or `timed_out` | Preserve other grounded successes as `partial` |
| Failed prerequisite | `skipped` with `dependency_failed` | Do not invoke the dependent agent |
| User cancellation | `cancelled` | Cancel active sends and mark pending work cancelled |
| Overall deadline | `timed_out` | Cancel unfinished sends; never report success |
| Missing evidence | `declined` | Discard ungrounded answer text |
| Grounded disagreement | successful claims marked conflicting | Return `partial` with visible conflict notice |

Results are deterministically ordered by plan step, regardless of completion
order. Synthesis preserves per-claim agent and source provenance, typed partial
failures, omissions, and conflicts.

## Offline certification

Run the labelled selection and exact-DAG gate:

```powershell
. .\scripts\dev_env.ps1
uv run python .\scripts\run_orchestration_eval.py --semantic offline
```

The ten-case suite covers single-agent work, independent and sequential squads,
`after` ordering, ambiguity, no eligible agent, two-agent dependencies, a
five-agent parallel squad, and a five-agent chain. It passes only when exact
selected-agent set and exact DAG are both `1.000` with zero false positives.
Machine-readable reports are written under
`reports\evals\orchestration\`.

Executor controls are covered by:

```powershell
uv run pytest .\tests\orchestrate -q
```

The complete repository gate runs discovery and orchestration evaluations
together with the five-hero tool-call gate:

```powershell
pwsh .\scripts\verify.ps1
```

The hero gate can also run independently:

```powershell
uv run python .\scripts\run_tool_call_eval.py
```

It covers all 20 narrow tools, strict required/extra argument rejection, exact
tool selection, no opaque catch-all operation, fixture provenance, and
content-safe tool telemetry. Its deterministic arguments are labelled
evaluation fixtures rather than represented as live model output. Reports are
written under `reports\evals\tool-calls\`.

Run the separate live D10 gate:

```powershell
pwsh .\scripts\certify_foundry_orchestration.ps1
```

It selects Demand Forecasting and Manufacturing Operations dynamically from the
20-agent catalog, executes both through native Foundry A2A, retrieves
invocation-bound Foundry IQ evidence, and requires observed concurrency plus
complete grounded synthesis. The content-safe certificate stores IDs, statuses,
durations, protocol, evidence counts, hashes, and telemetry under
`reports\evals\orchestration-live\latest.json`; it does not persist prompts or
answers.
