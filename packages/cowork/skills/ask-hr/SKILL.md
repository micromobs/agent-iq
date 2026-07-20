---
name: ask-hr
description: Resolve complex HR, benefits, onboarding, learning, recognition, and employee-listening intents with rich inline UX and approval-safe actions.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Ask HR

## Outcome

Provide an employee-services experience that can move from a life event or
people question to policy guidance, a plan, a comparison, and a safe next
action. The HR squad includes:

- `employee-hr-concierge`
- `employee-benefits-advisor`
- `employee-onboarding`
- `employee-learning`
- `employee-recognition`
- `employee-listening`

The provider is **Contoso Employee Services**. Use only the people, policies,
balances, plans, and survey results returned for the current request.

## Tool flow

Use `list_employee_scenarios` when the user asks to browse HR workflows. For a direct
question such as parental leave, open enrollment, first-day setup, career
planning, peer recognition, or pulse-survey follow-up, call
`resolve_employee_intent`. When the user selects a known experience, call
`open_employee_experience`.

Render the returned `selectedAgents` and explain each public selection reason.
Show `phases` as observable work, then compose `metrics`, `sections`, `sources`,
and `actions`. Do not show hidden chain-of-thought. A benefits comparison should
use the comparison table; leave planning should use a timeline and checklist;
onboarding should use a cross-service checklist; career planning should use a
path or decision tree; listening should use charts with constructive follow-up.

Use Work IQ only when the user asks to ground the conversation in their real
calendar, files, messages, or organization. Security-trim it and keep real M365
results source-specific and permission-scoped.

## Safe action roundtrip

For any returned draft or approval action, call `prepare_employee_action` using
the exact scenario and action IDs. Send only bounded form values that the user
reviewed. Render the receipt's `actionClass`, named approver, next steps, and
disclosure. `executed=false` must remain visible.

The experience may prepare a leave-plan draft, dependent-change checklist,
benefits comparison, welcome message, learning plan, recognition note, or
manager action plan. It must not submit leave, alter benefits, change employee
records, enroll a course, send praise, or publish survey commitments. A
`send_message` action is still a draft; a `read_only_tool` action is still
non-mutating; a `local_ui` action stays inside the card.

## Example scenarios

- A new parent asks for parental leave, benefit implications, manager handoff,
  payroll timing, and a return-to-work checklist.
- A new hire asks for day-one tasks across HR, IT, payroll, campus, and learning.
- An employee compares medical plans after a marriage life event and prepares a
  review checklist without claiming enrollment.
- A manager reviews pulse-survey themes, sees trend charts, and drafts an action
  plan with owners and dates.
- An employee asks for a path to senior engineer, receives skill gaps, courses,
  mentors, and a review-ready development plan.

## Response contract

Lead with the policy-backed answer, then the visual plan and action boundary.
Preserve citations and source titles in `sources`. Surface conflicts, missing
eligibility facts, and approval requirements. Never invent policy, balances,
medical advice, compensation decisions, or authenticated identity.
