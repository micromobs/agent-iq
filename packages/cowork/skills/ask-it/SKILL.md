---
name: ask-it
description: Triage IT, device, identity, meeting-readiness, workplace, and site-setup requests through a visible multi-agent experience with safe escalation drafts.
license: MIT
metadata:
  author: Agent IQ
  version: "1.5.1"
---

# Ask IT

## Outcome

Create a fast, visual IT rescue experience that explains what is being checked,
what the employee can do now, and what requires a service owner. Expected
specialists:

- `employee-it-helpdesk`
- `employee-identity-security`
- `employee-campus-services`
- `employee-site-provisioning`

Service details are supplied by **Contoso Employee Services**.

## Tool flow

For a plain VPN or remote-access request such as "My VPN is down", "I cannot
connect remotely", or "VPN failed before my meeting", call `orchestrate`
exactly once with the user's request. Do not call `search_agents` first and do
not force a fixed router. Agent IQ must dynamically select exactly one eligible
IT specialist, invoke it over native A2A, and return one compact read-only
guidance flow.

For other IT requests—broken cameras, lost devices, unexpected MFA prompts,
desk/room needs, or Teams/SharePoint site requests—call
`resolve_employee_intent`. Use `list_employee_scenarios` for a browseable IT
workflow gallery and `open_employee_experience` when a scenario card is
selected.

For scenario-driven Employee Operations results, show the returned
`selectedAgents` first so a VPN-plus-security request visibly forms the minimum
squad. Render `phases` as a public troubleshooting and escalation graph. Use
`sections` for diagnostics, step-by-step checklists, decision trees, device
cards, campus maps, approvals, and policy findings. Keep `sources` and
`actions` attached to the relevant diagnosis rather than burying them in prose.

If the request depends on real service-health messages, meetings, or files, use
Work IQ as a permission-trimmed companion and keep each source explicit. Do not claim
the experience queried Intune, Entra, ServiceNow, SharePoint, or a
device unless a real connector result is present.

## Action safety

Use `prepare_employee_action` only for a returned action ID. Show `actionClass`
and the receipt state. `executed=false` means the workflow has not reset MFA,
disabled a device, restored access, opened a ticket, booked a room, or created a
site. It may prepare an incident summary, security escalation, booking draft,
or provisioning review request.

For a lost device, clearly separate immediate human steps from enterprise
operations: disconnect if possible, contact the service desk, review account
activity, and route the draft to the named approver. Never claim remote wipe or
account revocation. For blocked access, name the policy and evidence rather than
suggesting a bypass.

## Example scenarios

- "My VPN is down" calls `orchestrate` once so Agent IQ can select exactly one
  IT specialist and return aggregate read-only recovery guidance with one source.
- "My VPN is down and my customer call starts in ten minutes" still uses the
  plain VPN flow unless the user adds a different security or workplace need.
- "I lost my laptop and see an MFA prompt from another country" forms IT plus
  Identity & Security, shows parallel containment phases, and prepares a
  security review request.
- "Create a project site with external guests" combines template selection,
  data-classification questions, access policy findings, and named approval.
- "Find a desk near my team and the nearest wellness room" shows a campus map and
  a reservation draft without claiming a booking.

## Response contract

Be calm and operational. Distinguish advice, observed signals,
blocked steps, and approvals. Preserve the exact status and source citations.
Never expose credentials, raw tool arguments, hidden reasoning, or unsupported
service state.
