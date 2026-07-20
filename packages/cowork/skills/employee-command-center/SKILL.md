---
name: employee-command-center
description: Build a rich, personalized employee command center across tasks, company news, organization context, approvals, and the next best actions. Use for "my day", "what should I focus on", executive dashboard, or cross-service employee requests.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Employee Command Center

## Outcome

Turn a broad employee intent into one visual, explainable command center using the
30-scenario Agent IQ employee catalog. The primary specialists are:

- `employee-personal-dashboard`
- `employee-task-hub`
- `employee-news`
- `employee-org-directory`

Employee people, figures, agents, and documents are provided through **Contoso
Employee Services**. Keep every result attached to its returned source.

## Tool flow

1. If the user asks what Agent IQ can do, call `list_employee_scenarios` and
   present the searchable scenario gallery. Do not invent scenarios outside the
   returned catalog.
2. For a natural-language request, call `resolve_employee_intent` with the user's
   exact goal. It returns the best scenario and the complete presentation.
3. If the user picks a gallery card or names a scenario ID, call
   `open_employee_experience`.
4. Render the returned `selectedAgents`, `phases`, `metrics`, `sections`,
   `sources`, and `actions` directly. Preserve status labels, dependency order,
   data scope, and trust boundary.
5. Use Work IQ separately when the request refers to the user's real meetings,
   files, mail, or tasks. Permission-trim those results. Never imply that raw
   M365 content was passed to an employee tool whose schema only accepts intent
   or scenario ID.
6. For a draft or approval control, call `prepare_employee_action` with the
   selected scenario ID, returned action ID, and only the bounded form values
   visible in the experience.

## Visual composition

Lead with the hero title and status, then animate the agent formation from
`selectedAgents`. Show `phases` as a dependency-aware execution rail rather than
hidden reasoning. Use the section kind as the rendering contract: dashboard
tiles, timelines, forms, comparison tables, checklists, charts, people cards,
org charts, maps, galleries, document callouts, decision trees, approvals,
policy findings, source drawers, and follow-up chips.

For "Show me my day", combine focus cards, due tasks, relevant news, approval
risks, and suggested prompts. For a leader dashboard, prioritize business
signals, blocked work, owner/due-date callouts, and drill-through actions. Keep
the answer scannable enough for an inline card while preserving a fullscreen
path for the full experience.

## Actions and trust

The action receipt exposes `actionClass` as `local_ui`, `send_message`, or
`read_only_tool`. Display its state as prepared, review required, or not
executed. `executed=false` is mandatory: a receipt is never proof that leave,
payroll, expense, access, payment, provisioning, policy, or records data changed.
Name the approver returned by the tool and keep the "not submitted to a system
of record" language visible.

Local source, export, and follow-up controls may run in the host without an
enterprise write. Message and tool controls create drafts or review requests
only. Never turn a disabled or blocked control into a success-shaped fallback.

## Example prompts

- "Show me my day: urgent tasks, relevant company news, and approvals at risk."
- "Build a personal dashboard for my first week and show who can unblock me."
- "What should I focus on before my leadership meeting this afternoon?"
- "Show the organization around the payments platform and my open onboarding work."

If evidence is missing, say so. Keep source links in `sources`; never fabricate
M365 context, hidden agent work, approvals, or execution.
