---
name: corporate-services
description: Orchestrate personalized news, tasks, dashboards, organization discovery, onboarding, visual Q&A, campus services, learning, recognition, and employee listening.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Corporate Services

## Outcome

Deliver a visual employee-services concierge spanning:

- `employee-news`
- `employee-task-hub`
- `employee-personal-dashboard`
- `employee-org-directory`
- `employee-onboarding`
- `employee-visual-qa`
- `employee-campus-services`
- `employee-learning`
- `employee-recognition`
- `employee-listening`

The full catalog is provided through **Contoso Employee Services**.

## Tool flow

Call `list_employee_scenarios` when the user wants a gallery of corporate
service workflows. Call `resolve_employee_intent` for a natural-language request and
`open_employee_experience` for a selected card. Use the returned
`selectedAgents` to show why the request formed one specialist or a squad.
Render `phases`, `metrics`, `sections`, `sources`, and `actions` without
rewriting the scenario contract.

Work IQ is valuable for real personalized news, meetings, files, tasks, and
people context. Use it only with the user's authorization and security trimming.
Keep every result attached to its source and permission scope.
The employee tools themselves do not accept raw M365 content.

Use each section kind for a memorable inline experience: a news gallery with
topic chips; a My Day dashboard; task timelines and checklists; people cards and
an org chart; a campus map; visual policy Q&A callouts; an onboarding checklist;
a career decision tree; recognition message preview; or a pulse-survey chart
with manager actions.

## Action boundary

For returned draft controls, call `prepare_employee_action`. Display
`actionClass`, state, named approver, and `executed=false`. The workflow can prepare
a reminder, welcome message, room request, learning plan, recognition note, or
survey follow-up. It cannot send messages, book facilities, enroll training,
publish news, change reporting lines, or commit management actions.

Local UI controls can filter, expand, export, or open `sources`. Message actions
remain drafts. Read-only tool actions may assemble a package but do not mutate
an enterprise system.

## Showcase scenarios

1. "Show the company news relevant to my product area and explain why it matters."
2. "What are my urgent tasks, overdue approvals, and focus items today?"
3. "Who owns the payments API and how does that team report up?"
4. "Guide a new hire through HR, IT, payroll, campus, and learning."
5. "Use this photo to check the clean-desk policy and show remediation."
6. "Find a desk near my team, a wellness room, and lunch options on a campus map."
7. "Build a path from my current role to senior engineer with skills and courses."
8. "Draft a meaningful recognition note and show award options."
9. "Explain our pulse-survey trend and prepare a manager action plan."

## Response contract

Favor visual hierarchy over a long transcript. Preserve status, warnings,
citations, and gaps. Never invent a person, location, policy verdict, real task,
message delivery, or completed action.
