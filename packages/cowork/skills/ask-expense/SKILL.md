---
name: ask-expense
description: Handle receipt capture, reimbursement policy, travel disruption, flagged expenses, and approval preparation with visual evidence and explicit non-execution.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Ask Expense

## Outcome

Create a receipt-to-review experience that explains categorization, policy,
missing evidence, travel context, and the next safe action. The specialist team includes:

- `employee-expense`
- `employee-travel`
- `employee-legal-compliance`

All cards, receipts, itineraries, policies, and people are provided through
**Contoso Employee Services**.

## Tool flow

Use `resolve_employee_intent` for expense submission, receipt extraction,
reimbursement eligibility, flagged expenses, client meals, mileage, travel
disruption, or fare-policy questions. Use `list_employee_scenarios` to browse
the expense and travel workflows and `open_employee_experience` after the user
selects one.

Animate the `selectedAgents` formation when an intent combines travel and
expense. Show `phases` for evidence extraction, categorization, policy checks,
exception analysis, and approval preparation. Use `sections` to render receipt
document callouts, editable form fields, policy decision trees, comparison
tables, timelines, approval cards, and source drawers. Preserve `sources` and
`actions`.

If the user supplies a real receipt or itinerary through an authorized host,
keep that content permission-scoped and source-specific. Work IQ may retrieve a
related meeting or file, but do not claim the
employee tool accessed a card feed, expense platform, or travel provider.

## Safe action roundtrip

Call `prepare_employee_action` only with an action returned by the selected
scenario. Show the receipt's `actionClass` and state. Keep `executed=false`
visible beside the primary action.

The experience may prepare an expense-report draft, missing-receipt note,
policy-exception request, approver message, or travel rebooking checklist. It
must not submit reimbursement, charge a card, rebook a flight, approve an
exception, or pay the employee. A review-required receipt means a named human
must act in an authenticated system of record.

Never infer sensitive card digits or fabricate receipt fields. Mark uncertain
OCR, missing attendees, absent business purpose, or unsupported policy
exceptions as gaps that the user must review.

## Example scenarios

- "Submit last night's client dinner" extracts the available receipt fields,
  validates attendees and business purpose, flags the policy threshold, and
  prepares approval.
- "My flight was cancelled and I need to expense a hotel" forms Travel plus
  Expense, shows disruption options, policy limits, and two coordinated drafts.
- "Why was this expense flagged?" displays exact rule evidence, the affected
  line, remediation options, and the required approver.
- "Compare mileage versus rail for this trip" uses cost and policy columns
  without claiming a booking.

## Response contract

Give a concise eligibility or readiness result, then the rich evidence view.
Cite policy and source documents in `sources`. Never claim payment,
submission, booking, or approval.
