# Agent IQ Employee Operations demo

Agent IQ turns one employee intent into a visible specialist squad, an
explainable execution graph, rich inline UI, cited synthetic evidence, and a
safe action receipt. One Python contract powers both delivery surfaces:

- **MCP App:** `ui://agent-iq/employee-operations.html`

The showcase contains **26 sample agents, 30 scenarios, 30 synthetic knowledge
sources, 16 rich section kinds, and 3 safe action classes**.

## Run the showcase

From the `agent-iq` root:

```powershell
# Self-contained strict-host browser preview
.\scripts\run_employee_showcase.ps1 -Mode preview

# Five-act terminal rehearsal using the same authoritative models
.\scripts\run_employee_showcase.ps1 -Mode rehearse

# Browse or resolve exact examples
.\scripts\run_employee_showcase.ps1 -Mode catalog
.\scripts\run_employee_showcase.ps1 -Mode intent -Intent "My VPN is down before a client meeting"
.\scripts\run_employee_showcase.ps1 -Mode scenario -Scenario site-health-governance

# Prepare, but never execute, an action
.\scripts\run_employee_showcase.ps1 -Mode action `
  -Scenario expense-smart-submit `
  -ActionId ess-request-approval `
  -Field "business-purpose=Synthetic client workshop" `
  -Confirm
```

The browser preview is exported to `dist/employee-showcase/` and defaults to
`http://127.0.0.1:8765`. It simulates a strict MCP Apps host, including
initialization, server tool calls, size changes, fullscreen, theme changes,
gallery navigation, and action receipts.

## Five-minute money-shot script

For a presenter-ready walkthrough that starts with a simple leave-balance card
and progressively adds VPN diagnosis, grounded payroll, governed expenses,
parallel travel, onboarding, and policy enforcement, use
[`EMPLOYEE_OPERATIONS_PROGRESSIVE_DEMO_SCRIPT.md`](EMPLOYEE_OPERATIONS_PROGRESSIVE_DEMO_SCRIPT.md).

### 0:00 - Start with the gallery

Open the preview. Say:

> "This is not a collection of thirty hard-coded widgets. It is a single
> scenario-contract-driven employee-operations renderer."

Search for **payroll**, **VPN**, **expense**, and **blocked access**. Point out
the 30 scenarios, three agent groups, synthetic disclosure, and governed-action
boundary.

### 0:35 - My Day command center

Open **My Day**.

Show:

- specialist formation for dashboard, tasks, news, and organization context;
- KPI cards, focus items, timeline, task checklist, relevant news, and sources;
- inline-to-fullscreen transition;
- the public execution stages rather than private model reasoning.

Say:

> "Agent IQ shows why each specialist joined and what public stage it completed.
> It never exposes hidden chain-of-thought."

### 1:20 - Complex HR life event

Prompt:

> "Create a parental-leave plan across HR, Benefits, Payroll, IT, and my manager."

Show the multi-agent formation, leave timeline, benefit implications, payroll
milestones, manager handoff, checklist, and cited policy documents.

Click the primary action. The receipt must say **prepared** or **review
required**, name the approver, and show **No enterprise action was executed**.

### 2:10 - IT meeting rescue

Prompt:

> "My VPN is down and my customer meeting starts in ten minutes."

Show a time-sensitive troubleshooting rail, decision tree, quick alternatives,
and escalation draft. Emphasize that the UI does not falsely claim a ticket,
network repair, or device change.

### 2:55 - Travel plus expense

Prompt:

> "My flight was cancelled and I need to expense a hotel tonight."

Show two specialists forming from one intent, parallel travel and expense
stages, policy comparisons, missing-evidence callouts, and coordinated safe
actions.

### 3:40 - Governance that explains "why"

Open **Site Health & Governance** or prompt:

> "Explain why my access to the finance site is blocked and show the approved remediation."

Show:

- exact policy and affected user/resource;
- evidence and unmet control;
- blocked status, severity, owner, and due date;
- required approver and remediation draft;
- no bypass, silent disablement, or success-shaped fallback.

### 4:35 - Close on the architecture

Open Sources and then return to the gallery.

Say:

> "The same Python models drive the remote MCP App, the Cowork skills, the
> action receipts, and the regression tests. We can add a scenario without
> building another bespoke UI."

## Scenario matrix

| ID | Scenario | Domain | Signature UX |
| --- | --- | --- | --- |
| `my-day` | My Day | Command center | KPI dashboard, tasks, news, focus rail |
| `new-hire-launchpad` | New Hire Launchpad | Onboarding | Cross-service checklist, people cards, campus links |
| `parental-leave-planner` | Parental Leave Planner | Time off | Milestone timeline, policy callouts, approval |
| `benefits-open-enrollment` | Benefits Open Enrollment | Benefits | Plan comparison, cost chart, decision tree |
| `payslip-explainer` | Payslip Explainer | Payroll | Document callouts, gross-to-net dashboard |
| `paycheck-discrepancy` | Paycheck Discrepancy | Payroll | Delta chart, discrepancy form, specialist review |
| `promotion-comp-change` | Promotion Compensation Change | Compensation | Compensation comparison, approvals, timeline |
| `time-off-coverage` | Time Off Coverage Planner | Time off | Team coverage checklist, calendar timeline |
| `expense-smart-submit` | Expense Smart Submit | Expense | Receipt form, policy checks, approval draft |
| `client-dinner-policy` | Client Dinner Policy Check | Expense | Eligibility decision tree, exact policy finding |
| `travel-disruption` | Travel Disruption Rebooking | Travel | Itinerary timeline, option comparison, safe drafts |
| `offboarding-command-center` | Offboarding Command Center | Offboarding | HR/IT/security checklist, access milestones |
| `vpn-meeting-rescue` | VPN Meeting Rescue | IT | Troubleshooting rail, decision tree, escalation |
| `lost-device-response` | Lost Device Response | Security | Parallel containment, account/device checklist |
| `software-access-governance` | Software Access Governance | Security | Policy finding, entitlement evidence, remediation |
| `laptop-refresh` | Laptop Refresh Eligibility | Device | Device cards, eligibility comparison, request draft |
| `cafeteria-wellness` | Cafeteria & Wellness Picks | Workplace | Menu gallery, wellness cards, campus context |
| `room-desk-reservation` | Room & Desk Reservation | Workplace | Campus map, room comparison, booking draft |
| `praise-colleague` | Praise a Colleague | Recognition | Message preview, award cards, trend context |
| `pulse-survey-action` | Pulse Survey Action Plan | Listening | Sentiment chart, themes, manager checklist |
| `sales-customer-360` | Sales Customer 360 | Sales | Account KPI dashboard, engagement timeline |
| `month-end-close` | Month-End Close Tracker | Finance | Close dashboard, control timeline, blocked owners |
| `purchase-vendor-compare` | Purchase Vendor Comparison | Procurement | Vendor table, policy findings, approval |
| `contractor-onboarding` | Contractor Onboarding | Contractor | Identity/access/site checklist and expiration |
| `personalized-news` | Personalized Company News | Communications | Relevant-news gallery and source drawer |
| `org-explorer` | Org Explorer | Organization | People cards, org chart, role context |
| `visual-policy-qa` | Visual Policy Q&A | Visual Q&A | Image-derived callouts and remediation tree |
| `learning-career-path` | Learning & Career Path | Learning | Skill-gap chart, course cards, career path |
| `site-provisioning` | Site Provisioning Request | Governance | Template comparison, sharing controls, approval |
| `site-health-governance` | Site Health & Governance | Governance | Health dashboard, policy findings, records risks |

## Rich UI contract

The single scenario-contract-driven renderer supports:

1. `dashboard`
2. `timeline`
3. `form`
4. `comparison_table`
5. `checklist`
6. `chart`
7. `people_cards`
8. `org_chart`
9. `map`
10. `gallery`
11. `document_callouts`
12. `decision_tree`
13. `approvals`
14. `policy_findings`
15. `source_drawer`
16. `follow_ups`

Every experience also includes selected agents, dependency-aware public phases,
metrics, citations, suggested prompts, synthetic disclosure, and a trust
boundary.

## Action model

Every action has a specific kind and one of three classes:

| Action class | Meaning |
| --- | --- |
| `local_ui` | Filter, navigate, expand, export view, or open sources in the host |
| `send_message` | Prepare a human message or approval request |
| `read_only_tool` | Prepare a draft/tool result without mutating a system |

Receipts can only be:

- `prepared`
- `review_required`
- `not_executed`

`executed` is always `false`. Leave, payroll, expense, access, payment, site,
policy, and records mutations require a trusted authenticated system-of-record
response outside this demo.

## Cowork package

The Agent IQ package now advertises 15 skills:

- six consumer-product skills;
- Vacation Balance;
- Employee Command Center;
- Ask HR;
- Ask IT;
- Ask Payroll;
- Ask Finance;
- Ask Expense;
- Corporate Services;
- Management & Governance.

Build:

```powershell
pwsh .\scripts\agent-iq.ps1 package -AllowAnonymousDemoConnector
```

Artifact:

```text
%LOCALAPPDATA%\AgentIQ\environments\dev\agent-iq.zip
```

Upload and deployment in the Microsoft 365 Admin Center remain operator steps.
The package is generated for the current environment; do not reuse a package
from another tenant.

## Safety and demo truth

- All employee data is synthetic and visibly labeled.
- Observable stages are orchestration metadata, not chain-of-thought.
- Missing evidence remains visible.
- Blocked access always names the policy, evidence, unmet control, affected
  resource, and approver.
- No receipt is proof of enterprise execution.
- Real M365 context from Work IQ must remain permission-trimmed and visually
  distinct from synthetic scenario data.
