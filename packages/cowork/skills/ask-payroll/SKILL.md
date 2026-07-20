---
name: ask-payroll
description: Explain payslips, deductions, pay timing, life-event impacts, and discrepancy triage with transparent calculations and non-executing case drafts.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Ask Payroll

## Outcome

Turn a confusing paycheck into a clear visual breakdown, policy context, and a
review-ready discrepancy package. The specialist squad is:

- `employee-payroll`
- `employee-hr-concierge`
- `employee-benefits-advisor`

All values and policies come from **Contoso Employee Services**. Keep policy
guidance distinct from financial, tax, or legal advice.

## Tool flow

Use `resolve_employee_intent` for payslip explanations, missing pay, deduction
changes, direct-deposit timing, benefit-withholding questions, or life-event pay
impacts. Use `list_employee_scenarios` to show payroll-related workflow choices and
`open_employee_experience` for a selected scenario.

Render `selectedAgents` so the user can see whether the question needs only
Payroll or also HR and Benefits. Present `phases` as source review, calculation,
policy check, discrepancy detection, and review preparation. Use dashboard
cards for gross/net totals, comparison tables for current-versus-prior pay,
charts for deduction deltas, document callouts for payslip lines, and forms for
the minimum correction details. Keep `sections`, `sources`, and `actions`
connected to each material figure.

Work IQ may locate a real approved payslip or HR communication only when the
host is authorized. Do not copy or expose sensitive pay data beyond what the
user requested, and keep Work IQ results linked to their own sources.

## Action boundary

Call `prepare_employee_action` for the exact discrepancy or review action
returned by the scenario. The receipt must show `actionClass`, required
approver, submitted fields, and `executed=false`. A prepared case is not a
payroll correction, direct-deposit update, tax change, benefit enrollment, or
payment confirmation.

Use only bounded inputs such as pay period, line-item label, expected
amount, observed amount, and employee note. Do not request bank account numbers,
tax identifiers, credentials, or unnecessary personal data. A `send_message`
action is a draft to a payroll specialist; a `read_only_tool` action may format
a comparison; a `local_ui` action may open sources or export the summary.

## Example scenarios

- "Why is my paycheck lower this month?" shows gross-to-net cards, a deduction
  delta chart, benefit and tax callouts, and a discrepancy decision tree.
- "How will parental leave affect my next two pay periods?" combines HR policy,
  benefits, and payroll timing in a milestone timeline.
- "My bonus is missing" distinguishes eligibility, payment date, withholding,
  and missing-data gaps before preparing a specialist review.
- "Explain every line on this payslip" produces clickable document callouts and
  a plain-language glossary.

## Response contract

Show the math and assumptions. Cite every policy or source in
`sources`. If the data cannot prove an error, say that the result is
inconclusive. Never promise a correction date or claim money moved.
