---
name: ask-finance
description: Coordinate finance close, controls, procurement, contract-policy, and customer-context scenarios with dashboards, comparisons, approvals, and safe drafts.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Ask Finance

## Outcome

Build an executive-ready finance experience that moves from question to
evidence, control status, options, and named approval without claiming a
transaction. Expected specialists:

- `employee-finance`
- `employee-procurement`
- `employee-legal-compliance`
- `employee-sales-customer360`

The data plane is **Contoso Employee Services**.

## Tool flow

Call `resolve_employee_intent` for month-end close, outstanding controls,
variance triage, purchase requests, vendor comparisons, contract terms, client
gift policy, pipeline context, or customer-health questions. Use
`list_employee_scenarios` for the finance gallery and
`open_employee_experience` for an exact scenario.

Show `selectedAgents` and the minimum public reason for each. Render `phases` as
an auditable sequence or parallel plan: collect evidence, check controls,
compare options, identify exceptions, and prepare review. Use KPI dashboards,
close timelines, variance charts, vendor comparison tables, policy findings,
approval panels, and document callouts from `sections`. Preserve `sources` and
`actions` without fabricating ERP, CRM, procurement, or contract-system access.

When the user refers to a real meeting, file, email decision, or account update,
use Work IQ separately and permission-trim it. Keep every M365 and finance fact
attached to its own source.

## Actions and approvals

For a returned draft, call `prepare_employee_action` and display `actionClass`,
receipt state, named approver, next steps, and `executed=false`. The workflow may
prepare a purchase request, close-control follow-up, vendor recommendation,
contract-review packet, or account-owner message. It may not post a journal,
approve a control, create a purchase order, commit budget, sign a contract,
change CRM, or transfer funds.

Policy findings must name the affected object, evidence, unmet control, and
approver. Never hide a blocked path or silently loosen a threshold. If finance,
legal, and sales signals disagree, display the conflict and make the decision
conditional.

## Example scenarios

- A controller asks for close readiness: show completed and blocked controls,
  overdue owners, a trend chart, and a draft reminder package.
- A manager asks to buy a laptop fleet: compare vendors, price, warranty,
  sustainability, contract terms, and policy exceptions, then prepare approval.
- A sales leader asks whether to extend special terms to a customer: combine
  account health, pipeline, policy, and finance risk without making the decision.
- A user asks whether a client gift is allowed: show a decision tree, threshold
  evidence, required disclosure, and escalation draft.

## Response contract

Lead with the decision status, not decorative prose. Keep numbers qualified and
cited in `sources`. Surface gaps and stale data. Do not invent live balances,
ledger status, vendor bids, customer records, or authorization.
