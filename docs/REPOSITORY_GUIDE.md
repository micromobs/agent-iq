# Agent IQ Repository Guide

This generated index covers all **433** files in the release tree. Regenerate it with:

```powershell
uv run python .\scripts\generate_repository_guide.py
```

For task-oriented setup, start with the [README](../README.md), [Azure deployment guide](DEPLOY_TO_AZURE.md), and [Cowork installation guide](INSTALL_IN_COWORK.md).

## Inventory

| Area | Files |
| --- | ---: |
| `(root)` | 15 |
| `.github` | 15 |
| `apps` | 21 |
| `deployments` | 9 |
| `docs` | 34 |
| `evals` | 6 |
| `packages` | 25 |
| `schemas` | 37 |
| `scripts` | 52 |
| `src` | 142 |
| `tests` | 77 |

## .github

| Path | Purpose |
| --- | --- |
| `.github/dependabot.yml` | YAML configuration or workflow. |
| `.github/extensions/agent-iq-canvas/extension.mjs` | Javascript module for extension. |
| `.github/extensions/agent-iq-canvas/package.json` | JSON contract or configuration: agent-iq-canvas-extension. |
| `.github/extensions/agent-iq-canvas/runtime.mjs` | Javascript module for runtime. |
| `.github/extensions/agent-iq-canvas/runtime.test.mjs` | Javascript module for runtime test. |
| `.github/extensions/agent-iq-canvas/web/assets/index-CUlzHGM0.css` | Stylesheet for index CUlzHGM0. |
| `.github/extensions/agent-iq-canvas/web/assets/index-DegxbrJM.js` | Javascript module for index DegxbrJM. |
| `.github/extensions/agent-iq-canvas/web/demo/launch-command-center.agui.json` | JSON data with top-level fields: agUiReferenceRevision, canonicalEventCount, eventCount, events. |
| `.github/extensions/agent-iq-canvas/web/index.html` | HTML experience: Agent IQ Command Center. |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | YAML configuration: Bug report. |
| `.github/ISSUE_TEMPLATE/config.yml` | YAML configuration or workflow. |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | YAML configuration: Feature request. |
| `.github/skills/agent-iq/SKILL.md` | Documentation: Agent IQ engineering skill. |
| `.github/skills/deploy-agent-iq/SKILL.md` | Documentation: Deploy Agent IQ. |
| `.github/workflows/verify.yml` | YAML configuration: verify. |

## apps

| Path | Purpose |
| --- | --- |
| `apps/canvas/index.html` | HTML experience: Agent IQ Command Center. |
| `apps/canvas/package-lock.json` | JSON contract or configuration: @agent-iq/canvas. |
| `apps/canvas/package.json` | JSON contract or configuration: @agent-iq/canvas. |
| `apps/canvas/playwright.config.ts` | Typescript module for playwright config. |
| `apps/canvas/public/demo/launch-command-center.agui.json` | JSON data with top-level fields: agUiReferenceRevision, canonicalEventCount, eventCount, events. |
| `apps/canvas/scripts/check-build.mjs` | Javascript module for check build. |
| `apps/canvas/src/App.test.tsx` | React typescript module for App test. |
| `apps/canvas/src/App.tsx` | React typescript module for App. |
| `apps/canvas/src/host.ts` | Typescript module for host. |
| `apps/canvas/src/main.tsx` | React typescript module for main. |
| `apps/canvas/src/replay.test.ts` | Typescript module for replay test. |
| `apps/canvas/src/replay.ts` | Typescript module for replay. |
| `apps/canvas/src/styles.css` | Stylesheet for styles. |
| `apps/canvas/src/test-setup.ts` | Typescript module for test setup. |
| `apps/canvas/src/types.ts` | Typescript module for types. |
| `apps/canvas/tests/browser/accessibility.spec.ts` | Typescript module for accessibility spec. |
| `apps/canvas/tests/browser/employee-operations.spec.ts` | Typescript module for employee operations spec. |
| `apps/canvas/tests/browser/vpn-recovery.spec.ts` | Typescript module for vpn recovery spec. |
| `apps/canvas/tsconfig.json` | JSON data with top-level fields: compilerOptions, include. |
| `apps/canvas/vite.config.ts` | Typescript module for vite config. |
| `apps/employee-copilot/preview/index.html` | HTML experience: Agent IQ Employee Operations Preview. |

## deployments

| Path | Purpose |
| --- | --- |
| `deployments/container-apps/artifact-canary/Dockerfile` | Repository asset for Dockerfile. |
| `deployments/container-apps/orchestrator-canary/Dockerfile` | Repository asset for Dockerfile. |
| `deployments/copilot/build.py` | CLI wrapper for deterministic Agent IQ Copilot plugin builds. |
| `deployments/cowork/__init__.py` | Microsoft Cowork package build and certification tooling. |
| `deployments/cowork/build.py` | CLI wrapper for deterministic Agent IQ Cowork package builds. |
| `deployments/foundry/consumer/generate.py` | CLI wrapper for consumer Foundry hosted-agent generation. |
| `deployments/foundry/consumer/probe.py` | CLI wrapper for strict native-A2A consumer canary certification. |
| `deployments/foundry/consumer/validate.py` | CLI wrapper for consumer Foundry hosted-agent validation. |
| `deployments/foundry/host/Dockerfile` | Repository asset for Dockerfile. |

## docs

| Path | Purpose |
| --- | --- |
| `docs/architecture/ag-ui-projection.md` | Documentation: Governed AG-UI projection. |
| `docs/architecture/contracts.md` | Documentation: Product contracts. |
| `docs/architecture/match.md` | Documentation: Match. |
| `docs/architecture/overview.md` | Documentation: Agent IQ architecture. |
| `docs/decisions/0001-contract-first.md` | Documentation: ADR 0001: Contract-first architecture. |
| `docs/decisions/0002-native-a2a-only.md` | Documentation: ADR 0002: Native A2A only on the release path. |
| `docs/decisions/0003-canary-promotion.md` | Documentation: ADR 0003: Parallel canary and explicit promotion. |
| `docs/decisions/0004-structured-evidence.md` | Documentation: ADR 0004: Structured evidence is the citation authority. |
| `docs/decisions/0005-microsoft-boundaries.md` | Documentation: ADR 0005: Build on Microsoft systems of record. |
| `docs/DEMO_RUNBOOK.md` | Documentation: Demo runbook. |
| `docs/DEPLOY_TO_AZURE.md` | Documentation: Deploy Agent IQ to Azure. |
| `docs/EMPLOYEE_OPERATIONS_DEMO.md` | Documentation: Agent IQ Employee Operations demo. |
| `docs/EMPLOYEE_OPERATIONS_PROGRESSIVE_DEMO_SCRIPT.md` | Documentation: Agent IQ progressive employee-operations demo script. |
| `docs/INSTALL_IN_COWORK.md` | Documentation: Install Agent IQ in Microsoft Cowork. |
| `docs/operations/configuration.md` | Documentation: Configuration. |
| `docs/operations/copilot-canvas.md` | Documentation: GitHub Copilot Canvas. |
| `docs/operations/copilot-plugin.md` | Documentation: GitHub Copilot App plugin. |
| `docs/operations/discovery-evaluation.md` | Documentation: Discovery evaluation. |
| `docs/operations/foundry-capability-ledger.md` | Documentation: Foundry capability ledger. |
| `docs/operations/foundry-descriptor-host.md` | Documentation: Descriptor-driven Foundry host. |
| `docs/operations/foundry-hosted-generation.md` | Documentation: Foundry hosted-agent generation. |
| `docs/operations/native-a2a-runtime.md` | Documentation: Native A2A runtime. |
| `docs/operations/orchestration.md` | Documentation: Dynamic orchestration. |
| `docs/operations/orchestrator-canary.md` | Documentation: Orchestrator canary operations. |
| `docs/operations/structured-evidence.md` | Documentation: Structured evidence. |
| `docs/operations/typed-artifacts.md` | Documentation: Invocation-bound typed business artifacts. |
| `docs/operations/updated-demo.md` | Documentation: Demo operations. |
| `docs/provenance/sources.json` | JSON data with top-level fields: schemaVersion, sources. |
| `docs/REPOSITORY_GUIDE.md` | Documentation: Agent IQ Repository Guide. |
| `docs/SCENARIOS.md` | Documentation: Agent IQ scenarios. |
| `docs/screenshots/card-inner-outer.png` | PNG image asset (2160x582). |
| `docs/screenshots/card-single-surface.png` | PNG image asset (1334x480). |
| `docs/screenshots/vaca-bal-prompt.png` | PNG image asset (2406x597). |
| `docs/screenshots/vacation-balance-card.png` | PNG image asset (1634x529). |

## evals

| Path | Purpose |
| --- | --- |
| `evals/consumer_hero_tools_live.json` | JSON data with top-level fields: cases, schemaVersion, suiteId. |
| `evals/cowork_ux_baseline.jsonl` | JSON Lines dataset with 2 records. |
| `evals/cowork_ux_cases.jsonl` | JSON Lines dataset with 1 records. |
| `evals/foundry_native_suite.json` | JSON data with top-level fields: allowServiceManagedUnpricedUsage, cases, criteria, interactionPattern. |
| `evals/launch_command_center.json` | JSON data with top-level fields: intent, rehearsalPrompts, scenarioId, schemaVersion. |
| `evals/release_rubric.json` | JSON data with top-level fields: cases, judgments, observations, rubric. |

## packages

| Path | Purpose |
| --- | --- |
| `packages/copilot/.mcp.json` | JSON data with top-level fields: mcpServers. |
| `packages/copilot/agents/agent-iq.agent.md` | Documentation: Agent IQ. |
| `packages/copilot/plugin.json` | JSON contract or configuration: agent-iq. |
| `packages/copilot/README.md` | Documentation: Agent IQ Copilot plugin. |
| `packages/copilot/skills/orchestrate-agents/SKILL.md` | Documentation: Orchestrate agents. |
| `packages/copilot/skills/replay-execution/SKILL.md` | Documentation: Replay an Agent IQ execution. |
| `packages/cowork/assets/color.png` | PNG image asset (192x192). |
| `packages/cowork/assets/color32.png` | PNG image asset (32x32). |
| `packages/cowork/assets/outline.png` | PNG image asset (32x32). |
| `packages/cowork/manifest/manifest.json` | JSON data with top-level fields: $schema, accentColor, agentConnectors, agentSkills. |
| `packages/cowork/skills/ask-expense/SKILL.md` | Documentation: Ask Expense. |
| `packages/cowork/skills/ask-finance/SKILL.md` | Documentation: Ask Finance. |
| `packages/cowork/skills/ask-hr/SKILL.md` | Documentation: Ask HR. |
| `packages/cowork/skills/ask-it/SKILL.md` | Documentation: Ask IT. |
| `packages/cowork/skills/ask-payroll/SKILL.md` | Documentation: Ask Payroll. |
| `packages/cowork/skills/brand-comparison/SKILL.md` | Documentation: Brand comparison. |
| `packages/cowork/skills/corporate-services/SKILL.md` | Documentation: Corporate Services. |
| `packages/cowork/skills/employee-command-center/SKILL.md` | Documentation: Employee Command Center. |
| `packages/cowork/skills/formulation-brief/SKILL.md` | Documentation: Formulation brief. |
| `packages/cowork/skills/launch-readiness/SKILL.md` | Documentation: EU Cold-Water Launch Command Center. |
| `packages/cowork/skills/management-governance/SKILL.md` | Documentation: Management and Governance. |
| `packages/cowork/skills/regulatory-claim-review/SKILL.md` | Documentation: Regulatory claim review. |
| `packages/cowork/skills/single-agent-analysis/SKILL.md` | Documentation: Focused single-agent Foundry analysis. |
| `packages/cowork/skills/supply-readiness/SKILL.md` | Documentation: Supply readiness. |
| `packages/cowork/skills/vacation-balance/SKILL.md` | Documentation: Vacation balance. |

## Root files

| Path | Purpose |
| --- | --- |
| `.dockerignore` | Repository asset for dockerignore. |
| `.editorconfig` | Repository asset for editorconfig. |
| `.env.example` | Repository asset for env. |
| `.gitattributes` | Repository asset for gitattributes. |
| `.gitignore` | Repository asset for gitignore. |
| `.python-version` | Repository asset for python version. |
| `.secrets.baseline` | Repository asset for secrets. |
| `AGENTS.md` | Documentation: Agent IQ contributor guide. |
| `CONTRIBUTING.md` | Documentation: Contributing to Agent IQ. |
| `LICENSE` | Repository asset for LICENSE. |
| `pyproject.toml` | Toml configuration for pyproject. |
| `README.md` | Documentation: Agent IQ. |
| `SECURITY.md` | Documentation: Security. |
| `START_HERE.md` | Documentation: Start here. |
| `uv.lock` | Locked dependency data for uv. |

## schemas

| Path | Purpose |
| --- | --- |
| `schemas/v1/ag-ui-projection.schema.json` | JSON contract or configuration: AgUiProjection. |
| `schemas/v1/agent-task.schema.json` | JSON contract or configuration: AgentTask. |
| `schemas/v1/agent.schema.json` | JSON contract or configuration: AgentDescriptor. |
| `schemas/v1/artifact-envelope.schema.json` | JSON contract or configuration: ArtifactEnvelope. |
| `schemas/v1/demand-scenario.schema.json` | JSON contract or configuration: DemandScenario. |
| `schemas/v1/error.schema.json` | JSON contract or configuration: AgentIqError. |
| `schemas/v1/evaluation-replay.schema.json` | JSON contract or configuration: RubricReplayBundle. |
| `schemas/v1/evaluation-rubric.schema.json` | JSON contract or configuration: RubricDefinition. |
| `schemas/v1/evaluation-scorecard.schema.json` | JSON contract or configuration: RubricEvaluationReport. |
| `schemas/v1/event-envelope.schema.json` | JSON contract or configuration: EventEnvelope. |
| `schemas/v1/evidence-bundle.schema.json` | JSON contract or configuration: EvidenceBundle. |
| `schemas/v1/evidence-envelope.schema.json` | JSON contract or configuration: EvidenceEnvelope. |
| `schemas/v1/execution-plan.schema.json` | JSON contract or configuration: ExecutionPlan. |
| `schemas/v1/execution-summary.schema.json` | JSON contract or configuration: ExecutionSummary. |
| `schemas/v1/foundry-capability-ledger.schema.json` | JSON contract or configuration: CapabilityLedger. |
| `schemas/v1/foundry-evaluation-certificate.schema.json` | JSON contract or configuration: FoundryNativeEvaluationCertificate. |
| `schemas/v1/foundry-evaluation-suite.schema.json` | JSON contract or configuration: FoundryEvaluationSuite. |
| `schemas/v1/match-request.schema.json` | JSON contract or configuration: MatchRequest. |
| `schemas/v1/memory-episode-receipt.schema.json` | JSON contract or configuration: EpisodeReceipt. |
| `schemas/v1/memory-execution-record.schema.json` | JSON contract or configuration: ExecutionMemoryRecord. |
| `schemas/v1/memory-feedback-record.schema.json` | JSON contract or configuration: FeedbackMemoryRecord. |
| `schemas/v1/memory-preparation-request.schema.json` | JSON contract or configuration: MemoryPreparationRequest. |
| `schemas/v1/memory-snapshot.schema.json` | JSON contract or configuration: MemorySnapshot. |
| `schemas/v1/orchestration-command.schema.json` | JSON contract or configuration: OrchestrationCommand. |
| `schemas/v1/orchestration-presentation.schema.json` | JSON contract or configuration: OrchestrationPresentation. |
| `schemas/v1/orchestration-result.schema.json` | JSON contract or configuration: OrchestrationResult. |
| `schemas/v1/regulatory-decision.schema.json` | JSON contract or configuration: RegulatoryDecision. |
| `schemas/v1/sap-change-proposal.schema.json` | JSON contract or configuration: SapChangeProposal. |
| `schemas/v1/sap-read-result.schema.json` | JSON contract or configuration: SapReadResult. |
| `schemas/v1/search-presentation.schema.json` | JSON contract or configuration: AgentSearchPresentation. |
| `schemas/v1/selection-decision.schema.json` | JSON contract or configuration: SelectionDecision. |
| `schemas/v1/squad-decision-envelope.schema.json` | JSON contract or configuration: SquadDecisionEnvelope. |
| `schemas/v1/supply-commitment.schema.json` | JSON contract or configuration: SupplyCommitment. |
| `schemas/v1/synthesis-result.schema.json` | JSON contract or configuration: SynthesisResult. |
| `schemas/v1/tool-descriptor.schema.json` | JSON contract or configuration: ToolDescriptor. |
| `schemas/v1/vacation-balance.schema.json` | JSON contract or configuration: VacationBalance. |
| `schemas/v1/vpn-recovery-guidance.schema.json` | JSON contract or configuration: VpnRecoveryGuidance. |

## scripts

| Path | Purpose |
| --- | --- |
| `scripts/__init__.py` | Agent IQ operator and verification scripts. |
| `scripts/agent-iq.ps1` | Powershell automation for agent iq. |
| `scripts/build_cowork_canary.ps1` | Powershell automation for build cowork canary. |
| `scripts/build_live_artifact_certificate.py` | Build a content-safe certificate from fresh, live-bound artifact evidence. |
| `scripts/certify_foundry_evidence.ps1` | Powershell automation for certify foundry evidence. |
| `scripts/certify_foundry_hero_tools.ps1` | Powershell automation for certify foundry hero tools. |
| `scripts/certify_foundry_native_eval.ps1` | Powershell automation for certify foundry native eval. |
| `scripts/certify_foundry_orchestration.ps1` | Powershell automation for certify foundry orchestration. |
| `scripts/certify_launch_command_center.py` | Certify the five-specialist EU cold-water launch command center over Cowork MCP. |
| `scripts/certify_orchestrator_canary.py` | Remotely certify Agent IQ HTTP/SSE, MCP, and native A2A ingress parity. Transport certification accepts only an explicit deployment-writer report. The report is rejected when it is older than :data:`MAX_DEPLOYMENT_REPORT_AGE` (currently 30 minutes), and the current Azure Container App must agree with that report before a transport result can pass. |
| `scripts/check_distribution.py` | Reject runtime-generated report evidence from publishable archives. |
| `scripts/check_repo.py` | Fail when source contains local-only paths or credential literals. |
| `scripts/check_wheel.py` | Verify the wheel contains all Fabrikam Consumer Products synthetic demo assets. |
| `scripts/deploy_consumer_canary.ps1` | Powershell automation for deploy consumer canary. |
| `scripts/deploy_consumer_fleet.ps1` | Powershell automation for deploy consumer fleet. |
| `scripts/deploy_foundry_host_canary.ps1` | Powershell automation for deploy foundry host canary. |
| `scripts/deploy_orchestrator_canary.ps1` | Powershell automation for deploy orchestrator canary. |
| `scripts/dev_env.ps1` | Powershell automation for dev env. |
| `scripts/employee_showcase.py` | Build, preview, and rehearse the Agent IQ employee-operations showcase. |
| `scripts/foundry_login.ps1` | Powershell automation for foundry login. |
| `scripts/generate_canvas_demo.py` | Generate the content-safe five-agent Canvas replay artifact. |
| `scripts/generate_consumer_deployments.ps1` | Powershell automation for generate consumer deployments. |
| `scripts/generate_cowork_icons.py` | Generate the original Agent IQ Microsoft 365 app icon set. |
| `scripts/generate_repository_guide.py` | Generate the complete Agent IQ repository file guide. |
| `scripts/generate_schemas.py` | Write or verify the committed Agent IQ JSON Schema publication set. |
| `scripts/invoke_artifact_canary.py` | Invoke one hosted agent through native A2A with non-secret publication coordinates. |
| `scripts/probe_copilot_canvas.py` | Run the Agent IQ GitHub Copilot Canvas host-registration certificate. |
| `scripts/probe_copilot_plugin.py` | Run the Agent IQ GitHub Copilot plugin host certificate. |
| `scripts/probe_cowork_connector.ps1` | Powershell automation for probe cowork connector. |
| `scripts/probe_cowork_connector.py` | Probe the exact remote MCP contract advertised by the Agent IQ Cowork package. |
| `scripts/probe_foundry_capabilities.ps1` | Powershell automation for probe foundry capabilities. |
| `scripts/probe_mcp_contract.py` | Probe the public MCP handshake without invoking any consumer agent. |
| `scripts/provision_team_azure.ps1` | Powershell automation for provision team azure. |
| `scripts/run_cowork_ux_eval.py` | Grade saved Cowork UX observations against the versioned release gates. |
| `scripts/run_discovery_eval.py` | Run the deterministic or Azure-backed Agent IQ discovery quality gate. |
| `scripts/run_employee_showcase.ps1` | Powershell automation for run employee showcase. |
| `scripts/run_foundry_a2a_conformance.ps1` | Powershell automation for run foundry a2a conformance. |
| `scripts/run_foundry_a2a_conformance.py` | Run and persist the Agent IQ live Foundry native-A2A conformance gate. |
| `scripts/run_foundry_evidence_canary.py` | Certify the D8 structured-evidence channel against the live hero agent. |
| `scripts/run_foundry_native_eval.py` | Validate or run the versioned native Microsoft Foundry evaluation suite. |
| `scripts/run_live_hero_tool_canary.py` | Certify five hosted hero agents from native A2A through their named tools. |
| `scripts/run_live_orchestration_canary.py` | Certify a dynamic two-agent plan against live Foundry native A2A. |
| `scripts/run_offline_evals.ps1` | Powershell automation for run offline evals. |
| `scripts/run_orchestration_eval.py` | Run the deterministic Agent IQ selection and dependency-DAG gate. |
| `scripts/run_rubric_eval.py` | Replay sanitized transcripts and traces through the versioned release rubric. |
| `scripts/run_tool_call_eval.py` | Run the deterministic Agent IQ hero tool-call gate. |
| `scripts/run_updated_demo.ps1` | Powershell automation for run updated demo. |
| `scripts/serve_artifact_canary.py` | Serve one live, identity-bound artifact-publication certification grant. |
| `scripts/serve_orchestrator.py` | Run the isolated Agent IQ MCP + HTTP/SSE + A2A canary host. |
| `scripts/summarize_consumer_fleet.ps1` | Powershell automation for summarize consumer fleet. |
| `scripts/validate_consumer_assets.py` | Validate the complete consumer product asset set and print its stable fingerprint. |
| `scripts/verify.ps1` | Powershell automation for verify. |

## src

| Path | Purpose |
| --- | --- |
| `src/agent_iq/__init__.py` | Agent IQ product package. Importing this package is deliberately side-effect free. Runtime composition, configuration loading, identity, and network clients are all explicit. |
| `src/agent_iq/api/__init__.py` | Agent IQ MCP, HTTP, and event-stream surfaces. |
| `src/agent_iq/api/a2a.py` | Incoming native A2A adapter for the Agent IQ coordinator. |
| `src/agent_iq/api/artifacts.py` | Authenticated HTTP boundary for invocation-bound artifact publication. |
| `src/agent_iq/api/host.py` | Live Agent IQ canary composition and readiness certification. |
| `src/agent_iq/api/http.py` | FastAPI and SSE ingress over the protocol-neutral Agent IQ service. |
| `src/agent_iq/api/mcp.py` | Cowork-facing MCP tools over the protocol-neutral Agent IQ service. |
| `src/agent_iq/api/mcp_apps.py` | Self-contained MCP App resources for Agent IQ. |
| `src/agent_iq/api/presentation.py` | Host-neutral presentation copy derived from canonical catalog records. |
| `src/agent_iq/api/service.py` | Host-independent orchestration service with lifecycle persistence. |
| `src/agent_iq/api/widgets/employee-operations.html` | HTML experience: Agent IQ Employee Operations. |
| `src/agent_iq/api/widgets/formation.html` | HTML experience: Agent IQ formation. |
| `src/agent_iq/api/widgets/historical-result.html` | HTML experience: Historical Agent IQ result. |
| `src/agent_iq/api/widgets/vacation-ring.html` | HTML experience: Agent IQ Vacation Balance. |
| `src/agent_iq/artifacts/__init__.py` | Typed business-artifact publication and authenticated companion delivery. |
| `src/agent_iq/artifacts/companion.py` | Identity-bound companion storage for validated business artifacts. |
| `src/agent_iq/catalog/__init__.py` | Canonical Agent IQ catalog and projections. |
| `src/agent_iq/catalog/adapters/__init__.py` | External catalog source adapters. |
| `src/agent_iq/catalog/adapters/foundry.py` | Microsoft Foundry hosted-agent catalog adapter. |
| `src/agent_iq/catalog/assets.py` | Validated source-data assets used to build the canonical catalog. |
| `src/agent_iq/catalog/cards.py` | Canonical authored and served Agent Card projections. |
| `src/agent_iq/catalog/live_demo.py` | Canonical catalog projections and inventory gates for live demos. |
| `src/agent_iq/catalog/loader.py` | Strict loaders and cross-file validation for canonical catalog assets. |
| `src/agent_iq/catalog/registry_projection.py` | Deterministic offline projection of the canonical agent catalog. |
| `src/agent_iq/config.py` | Validated, side-effect-free Agent IQ configuration. |
| `src/agent_iq/connectors/__init__.py` | Microsoft and external context connectors. |
| `src/agent_iq/connectors/entra.py` | Async bearer-token abstraction for keyless Azure connectors. |
| `src/agent_iq/connectors/foundry_toolbox.py` | Keyless access to a Foundry toolbox over its managed MCP endpoint. |
| `src/agent_iq/connectors/sap.py` | Governed SAP read boundary with an explicitly labelled fixture implementation. |
| `src/agent_iq/context/__init__.py` | Context binding and least-context planning. |
| `src/agent_iq/contracts/__init__.py` | Versioned, language-neutral Agent IQ contracts. |
| `src/agent_iq/contracts/agent.py` | Canonical agent, skill, source, health, and trust contracts. |
| `src/agent_iq/contracts/agui.py` | Safe AG-UI wire contracts projected from the canonical Agent IQ ledger. |
| `src/agent_iq/contracts/api.py` | Protocol-neutral northbound Agent IQ command and result contracts. |
| `src/agent_iq/contracts/artifacts.py` | Versioned typed business artifacts for invocation-bound companion delivery. |
| `src/agent_iq/contracts/base.py` | Shared primitives for versioned Agent IQ wire contracts. |
| `src/agent_iq/contracts/capabilities.py` | Versioned runtime capability-ledger contracts. |
| `src/agent_iq/contracts/errors.py` | Stable error codes and retry semantics. |
| `src/agent_iq/contracts/evaluation.py` | Versioned rubric, replay, and scorecard contracts. |
| `src/agent_iq/contracts/events.py` | Typed lifecycle events shared by Cowork, HTTP, SSE, and Canvas. |
| `src/agent_iq/contracts/evidence.py` | Structured evidence, claims, and globally normalized citations. |
| `src/agent_iq/contracts/execution.py` | Protocol-neutral orchestration execution results. |
| `src/agent_iq/contracts/match.py` | Intent matching, eligibility, scoring, and selection contracts. |
| `src/agent_iq/contracts/memory.py` | Agent IQ Memory preparation, decision, and write-back contracts. |
| `src/agent_iq/contracts/plan.py` | Transparent execution-plan and dependency-DAG contracts. |
| `src/agent_iq/contracts/presentation.py` | Compact host-facing projections over canonical Agent IQ audit contracts. |
| `src/agent_iq/contracts/sap.py` | Typed, provenance-preserving SAP read contracts. |
| `src/agent_iq/contracts/schemas.py` | Generate the versioned language-neutral JSON Schema publication set. |
| `src/agent_iq/contracts/synthesis.py` | Grounded multi-agent synthesis results. |
| `src/agent_iq/contracts/task.py` | Durable agent-task state and callback contracts. |
| `src/agent_iq/contracts/tools.py` | Framework-neutral contracts for narrow, governed agent tools. |
| `src/agent_iq/contracts/vacation.py` | Strict vacation-balance projection preserved across the text-only A2A boundary. |
| `src/agent_iq/contracts/vpn.py` | Strict VPN recovery guidance preserved across text-only A2A. |
| `src/agent_iq/data/__init__.py` | Versioned product data distributed with Agent IQ. |
| `src/agent_iq/data/consumer/agents.json` | JSON data with top-level fields: agents, catalogId, publishedAt, schemaVersion. |
| `src/agent_iq/data/consumer/corpora/consumer-market-brand-consumer.jsonl` | JSON Lines dataset with 12 records. |
| `src/agent_iq/data/consumer/corpora/consumer-product-innovation.jsonl` | JSON Lines dataset with 14 records. |
| `src/agent_iq/data/consumer/corpora/consumer-safety-regulatory-quality.jsonl` | JSON Lines dataset with 12 records. |
| `src/agent_iq/data/consumer/corpora/consumer-supply-manufacturing.jsonl` | JSON Lines dataset with 12 records. |
| `src/agent_iq/data/consumer/corpora/employee-expense.jsonl` | JSON Lines dataset with 1 records. |
| `src/agent_iq/data/consumer/corpora/employee-identity-security.jsonl` | JSON Lines dataset with 1 records. |
| `src/agent_iq/data/consumer/corpora/employee-it-helpdesk.jsonl` | JSON Lines dataset with 1 records. |
| `src/agent_iq/data/consumer/corpora/employee-vacation-leave.jsonl` | JSON Lines dataset with 1 records. |
| `src/agent_iq/data/consumer/discovery_cases.jsonl` | JSON Lines dataset with 42 records. |
| `src/agent_iq/data/consumer/knowledge_bases.json` | JSON data with top-level fields: bindings, schemaVersion. |
| `src/agent_iq/data/consumer/live_smoke.json` | JSON data with top-level fields: agents, schemaVersion. |
| `src/agent_iq/data/consumer/orchestration_cases.jsonl` | JSON Lines dataset with 17 records. |
| `src/agent_iq/data/consumer/runtime.json` | JSON data with top-level fields: agents, schemaVersion. |
| `src/agent_iq/data/consumer/sap_fixtures.json` | JSON data with top-level fields: generatedAt, records, schemaVersion, sourceLabel. |
| `src/agent_iq/data/consumer/tool_cases.jsonl` | JSON Lines dataset with 20 records. |
| `src/agent_iq/data/employee/agents.json` | JSON data with top-level fields: agents, catalogId, publishedAt, schemaVersion. |
| `src/agent_iq/data/employee/knowledge_sources.json` | JSON data with top-level fields: schemaVersion, sources. |
| `src/agent_iq/data/employee/scenarios.jsonl` | JSON Lines dataset with 30 records. |
| `src/agent_iq/deployments/__init__.py` | Reproducible deployment artifact generation and validation. |
| `src/agent_iq/deployments/consumer_knowledge.py` | Provision the canonical synthetic consumer Foundry IQ knowledge foundation. |
| `src/agent_iq/deployments/copilot_canvas_probe.py` | Certify Agent IQ Canvas registration in the real GitHub Copilot CLI. |
| `src/agent_iq/deployments/copilot_plugin.py` | Validate and deterministically build the Agent IQ GitHub Copilot plugin. |
| `src/agent_iq/deployments/copilot_probe.py` | Certify Agent IQ plugin loading in the real GitHub Copilot CLI. |
| `src/agent_iq/deployments/cowork.py` | Validate and deterministically build the Agent IQ Cowork canary package. |
| `src/agent_iq/deployments/foundry_canary.py` | Strict native-A2A certification for a Microsoft Foundry hosted-agent canary. |
| `src/agent_iq/deployments/foundry_capabilities.py` | Read-only Microsoft Foundry runtime capability probes. |
| `src/agent_iq/deployments/foundry_consumer.py` | Generate and validate current Foundry hosted-agent projects for the consumer fleet. |
| `src/agent_iq/deployments/foundry_host.py` | Image-based Foundry deployment for descriptor-selected Agent IQ hosts. |
| `src/agent_iq/employee/__init__.py` | Agent IQ Employee Operations sample data and presentation layer. The package owns read-only agent profiles, scenario walkthroughs, action preparation, and the compact Vacation projection consumed by the MCP surface and Microsoft 365 host adapters. |
| `src/agent_iq/employee/actions.py` | Safe, deterministic action preparation for the employee showcase. |
| `src/agent_iq/employee/assets.py` | Authoring-time asset schemas validated against the raw JSON data files. These wrap the presentation-layer building blocks from ``.contracts`` with catalog-level metadata (publish time, exact ID sets) so the on-disk JSON in ``agent_iq/data/employee`` is validated strictly at load time, before any presentation contract is ever built from it. |
| `src/agent_iq/employee/contracts.py` | Wire contracts for Agent IQ Employee Operations. Every model here describes a read-only employee-services surface. Nothing in this module represents (or is capable of triggering) a real write against any enterprise system: any action that resembles a write is always an approval-bound draft or message, never an executed side effect. These contracts follow the same strict/frozen/camelCase conventions as ``agent_iq.contracts`` (see ``agent_iq.contracts.base.ContractModel``) but are intentionally kept in their own package so the experience can evolve without touching the production contract surface. |
| `src/agent_iq/employee/loader.py` | Strict loaders, cross-file validation, and deterministic resolvers. This module is the only place that reads the employee showcase JSON data files from disk. Everything downstream (presentation building, intent matching) works from the validated, immutable Pydantic assets produced here. |
| `src/agent_iq/employee/vacation.py` | Compact Cowork projection for a vacation-balance request. |
| `src/agent_iq/errors.py` | Typed operational exceptions backed by stable Agent IQ error contracts. |
| `src/agent_iq/evals/__init__.py` | Evaluation result publication and local viewing. |
| `src/agent_iq/evals/cowork_ux.py` | Deterministic transcript-level quality gates for the Cowork experience. |
| `src/agent_iq/evals/foundry_native.py` | Native Microsoft Foundry evaluation adapter and content-safe certification. |
| `src/agent_iq/evals/live_artifacts.py` | Content-safe evaluation of live invocation-bound artifact publication. |
| `src/agent_iq/evals/live_tools.py` | Content-safe evaluation of live native-A2A hero tool traces. |
| `src/agent_iq/evals/orchestration.py` | Labelled evaluation of dynamic agent selection and dependency planning. |
| `src/agent_iq/evals/reporting.py` | Persist evaluation results as machine-readable JSON and a local HTML scorecard. |
| `src/agent_iq/evals/rubrics.py` | Fail-closed rubric evaluation over sanitized transcripts and execution traces. |
| `src/agent_iq/evals/tools.py` | Deterministic hero tool-selection and argument-schema evaluation. |
| `src/agent_iq/evidence/__init__.py` | Structured evidence retrieval, transport, and citation handling. |
| `src/agent_iq/evidence/a2a_shape.py` | Content-safe shape inspection for official A2A SDK response events. |
| `src/agent_iq/evidence/companion.py` | Identity-bound companion channel for structured invocation evidence. |
| `src/agent_iq/evidence/foundry_iq.py` | Keyless structured-source retrieval from a Foundry IQ knowledge base. |
| `src/agent_iq/evidence/normalize.py` | Global citation normalization backed only by structured evidence. |
| `src/agent_iq/evidence/resolver.py` | Identity-bound resolution of normalized evidence citations. |
| `src/agent_iq/match/__init__.py` | Security-trimmed intent matching and ranking. |
| `src/agent_iq/match/document.py` | Build and explain the public catalog text used by Match. |
| `src/agent_iq/match/engine.py` | Framework-neutral Match engine over canonical Agent IQ descriptors. |
| `src/agent_iq/match/evaluation.py` | Quantitative routing evaluation over labelled discovery cases. |
| `src/agent_iq/match/fusion.py` | Reciprocal-rank fusion of lexical and semantic retrieval. |
| `src/agent_iq/match/lexical.py` | Deterministic Okapi BM25 matching over canonical agent text. |
| `src/agent_iq/match/prefilter.py` | Eligibility filtering that runs before lexical or semantic retrieval. |
| `src/agent_iq/match/selector.py` | Deterministic single- and multi-clause agent selection. |
| `src/agent_iq/match/semantic.py` | Offline semantic matching and real Azure embedding support. |
| `src/agent_iq/mcp/__init__.py` | MCP tools exposing the protocol-neutral Agent IQ product core. |
| `src/agent_iq/mcp/artifact_tools.py` | MCP publication tool for pre-authorized validated business artifacts. |
| `src/agent_iq/orchestrate/__init__.py` | Single-agent and multi-agent planning and execution. |
| `src/agent_iq/orchestrate/context.py` | Least-context selection from pre-authorized context bindings. |
| `src/agent_iq/orchestrate/executor.py` | Bounded native-A2A execution of validated Agent IQ dependency plans. |
| `src/agent_iq/orchestrate/intent.py` | Deterministic public intent decomposition for execution planning. |
| `src/agent_iq/orchestrate/planner.py` | Catalog-driven execution planning over selected Agent IQ descriptors. |
| `src/agent_iq/orchestrate/synthesizer.py` | Deterministic grounded synthesis over normalized agent evidence. |
| `src/agent_iq/presentation/__init__.py` | Host-neutral presentation projections. |
| `src/agent_iq/presentation/agui.py` | One-way, content-safe projection from Agent IQ lifecycle events to AG-UI. |
| `src/agent_iq/publication.py` | Private artifact publication state carried across trusted runtime boundaries. |
| `src/agent_iq/py.typed` | Repository asset for py. |
| `src/agent_iq/runtime/__init__.py` | Agent invocation and durable task runtime. |
| `src/agent_iq/runtime/adapters/__init__.py` | Protocol-specific invocation adapters. |
| `src/agent_iq/runtime/adapters/native_a2a.py` | Strict Microsoft Foundry native-A2A runtime using the official A2A SDK. |
| `src/agent_iq/runtime/conformance.py` | Live native-A2A conformance evaluation with content-safe evidence. |
| `src/agent_iq/runtime/dispatcher.py` | Runtime dispatch boundary for native-only Demo 1 execution. |
| `src/agent_iq/runtime/hero_tools.py` | Build narrow LangChain tools from canonical Agent IQ runtime metadata. |
| `src/agent_iq/runtime/host_bootstrap.py` | Generic entry point for the reusable Agent IQ LangGraph host image. |
| `src/agent_iq/runtime/host_descriptor.py` | Validated logical-agent selection for reusable hosted runtime images. |
| `src/agent_iq/runtime/hosted_consumer.py` | Build one framework-selected consumer agent for Microsoft Foundry Hosted Agent Service. |
| `src/agent_iq/runtime/models.py` | Validated request and result models for native A2A invocation. |
| `src/agent_iq/runtime/pydantic_consumer.py` | PydanticAI hero runtime adapted to the Foundry Responses host boundary. |
| `src/agent_iq/runtime/telemetry.py` | Safe structured telemetry hooks for the native A2A runtime. |
| `src/agent_iq/telemetry/__init__.py` | Traces, metrics, audit correlation, and outcome signals. |
| `src/agent_iq/telemetry/configure.py` | Content-safe Azure Monitor OpenTelemetry bootstrap. |
| `src/agent_iq/tls.py` | TLS helpers for outbound calls from Foundry hosted runtimes. |
| `src/agent_iq/trust/__init__.py` | Identity, eligibility, policy, approval, and audit controls. |

## tests

| Path | Purpose |
| --- | --- |
| `tests/adapter_conformance/test_foundry_a2a.py` | Conformance tests for Foundry catalog access and native A2A invocation. |
| `tests/api/conftest.py` | Python source module. |
| `tests/api/test_a2a.py` | Python source module. |
| `tests/api/test_artifact_companion.py` | Python source module. |
| `tests/api/test_cowork_probe.py` | Python source module. |
| `tests/api/test_host.py` | Python source module. |
| `tests/api/test_http.py` | Python source module. |
| `tests/api/test_launch_command_center.py` | Python source module. |
| `tests/api/test_mcp.py` | Python source module. |
| `tests/api/test_service.py` | Python source module. |
| `tests/api/test_transport_parity.py` | Python source module. |
| `tests/artifacts/test_companion_artifacts.py` | Python source module. |
| `tests/artifacts/test_mcp_artifact_tool.py` | Python source module. |
| `tests/catalog/__init__.py` | Catalog tests. |
| `tests/catalog/test_consumer_assets.py` | Completeness and determinism gates for the canonical consumer assets. |
| `tests/catalog/test_live_demo.py` | Python source module. |
| `tests/catalog/test_loader_errors.py` | Catalog loaders reject malformed and inconsistent assets explicitly. |
| `tests/contract/__init__.py` | Contract tests. |
| `tests/contract/samples.py` | Representative valid Agent IQ contract instances. |
| `tests/contract/test_contracts.py` | Contract round-trip, invariant, and public-explanation tests. |
| `tests/contract/test_employee_showcase.py` | Focused contract tests for the isolated Agent IQ Employee Operations showcase. These tests exercise only ``agent_iq.employee`` (contracts, assets, loader) and the on-disk sample data under ``src/agent_iq/data/employee``. They do not touch, and are not touched by, the production MCP surfaces or any UI code. |
| `tests/contract/test_employee_validation.py` | Failure-path coverage for the isolated employee showcase contracts and loaders. |
| `tests/contract/test_execution_contract.py` | Python source module. |
| `tests/contract/test_memory_contracts.py` | Frozen WP-01 Memory and squad-decision contract invariants. |
| `tests/contract/test_schema_snapshots.py` | Committed JSON Schemas stay synchronized with contract models. |
| `tests/contract/test_tool_contracts.py` | Governed tool and SAP provenance contract tests. |
| `tests/contract/test_vpn_recovery.py` | Strict aggregate VPN recovery contract tests. |
| `tests/evals/__init__.py` | Evaluation publication tests. |
| `tests/evals/test_cowork_ux.py` | Python source module. |
| `tests/evals/test_foundry_native.py` | Python source module. |
| `tests/evals/test_live_artifacts.py` | Python source module. |
| `tests/evals/test_live_tools.py` | Live hero tool trace evaluation tests. |
| `tests/evals/test_orchestration.py` | Python source module. |
| `tests/evals/test_reporting.py` | Local Eval Center report tests. |
| `tests/evals/test_rubrics.py` | Python source module. |
| `tests/evals/test_tool_calls.py` | Hero tool-call selection, argument, and report gates. |
| `tests/evidence/test_normalization.py` | Python source module. |
| `tests/evidence/test_structured_evidence.py` | Structured evidence mapping, isolation, and A2A shape tests. |
| `tests/evidence/test_synthesis.py` | Python source module. |
| `tests/fixtures/wp01/memory-off-selection.json` | JSON data with top-level fields: candidateAgentIds, decisionSha256, request, schemaVersion. |
| `tests/match/__init__.py` | Match tests. |
| `tests/match/test_azure_embeddings.py` | Azure embedding adapter behavior without external network access. |
| `tests/match/test_components.py` | Lexical, semantic, fusion, selection, and prefilter component tests. |
| `tests/match/test_engine_quality.py` | The 20-agent consumer Match quality and determinism gates. |
| `tests/match/test_memory_off_baseline.py` | Immutable WP-01 memory-off routing baseline. |
| `tests/orchestrate/test_executor.py` | Python source module. |
| `tests/orchestrate/test_planning.py` | Python source module. |
| `tests/runtime/__init__.py` | Hosted runtime tests. |
| `tests/runtime/test_a2a_conformance.py` | Tests for live-conformance orchestration and safe report persistence. |
| `tests/runtime/test_foundry_toolbox.py` | Managed Foundry toolbox connector tests. |
| `tests/runtime/test_hero_tools.py` | Data-driven narrow hero-tool composition tests. |
| `tests/runtime/test_host_descriptor.py` | Reusable Foundry host descriptor tests. |
| `tests/runtime/test_hosted_consumer.py` | Fabrikam Consumer Products (synthetic demo) LangGraph hosted runtime tests. |
| `tests/runtime/test_pydantic_consumer.py` | PydanticAI hosted hero runtime tests. |
| `tests/runtime/test_runtime_utilities.py` | Runtime utility, dispatcher, model, and Entra adapter tests. |
| `tests/test_config.py` | Configuration safety tests. |
| `tests/test_package.py` | Package import contract. |
| `tests/unit/__init__.py` | Focused unit tests. |
| `tests/unit/test_agui_projection.py` | AG-UI projection, redaction, and deterministic replay tests. |
| `tests/unit/test_build_live_artifact_certificate.py` | Python source module. |
| `tests/unit/test_canary_report_writers.py` | Python source module. |
| `tests/unit/test_consumer_knowledge.py` | Canonical synthetic-consumer Foundry IQ provisioning tests. |
| `tests/unit/test_copilot_canvas_probe.py` | GitHub Copilot Canvas host-registration certificate tests. |
| `tests/unit/test_copilot_plugin.py` | Deterministic GitHub Copilot plugin packaging tests. |
| `tests/unit/test_copilot_probe.py` | GitHub Copilot plugin host-certificate tests. |
| `tests/unit/test_cowork_package.py` | Python source module. |
| `tests/unit/test_deployment_script_endpoints.py` | Static and offline endpoint-safety tests for Foundry deployment scripts. |
| `tests/unit/test_foundry_canary.py` | Native Foundry A2A canary certification tests. |
| `tests/unit/test_foundry_capabilities.py` | Foundry capability-ledger tests. |
| `tests/unit/test_foundry_host.py` | Descriptor-driven Foundry container host tests. |
| `tests/unit/test_foundry_manifest.py` | Current Foundry deployment generator tests. |
| `tests/unit/test_orchestrator_canary.py` | Python source module. |
| `tests/unit/test_release_policy.py` | Release boundaries for runtime-generated report evidence. |
| `tests/unit/test_serve_artifact_canary.py` | Python source module. |
| `tests/unit/test_team_deployment_cli.py` | Offline contract tests for the public Agent IQ deployment experience. |
| `tests/unit/test_telemetry_configure.py` | Python source module. |
| `tests/unit/test_updated_demo_launcher.py` | Static and offline safety checks for the Windows updated-demo launcher. |
