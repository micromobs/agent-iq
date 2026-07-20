---
name: management-governance
description: Explain policy enforcement, blocked access, site health, provisioning, records lifecycle, security, and compliance with exact evidence and approval-safe remediation.
license: MIT
metadata:
  author: Agent IQ
  version: "1.1.0"
---

# Management and Governance

## Outcome

Turn a blocked or risky request into an evidence-backed governance explanation
and a safe remediation path. Expected specialists:

- `employee-site-provisioning`
- `employee-policy-governance`
- `employee-access-governance`
- `employee-site-health`
- `employee-records-lifecycle`
- `employee-legal-compliance`
- `employee-identity-security`

The experience uses **Contoso Employee Services** evidence.

## Tool flow

Use `resolve_employee_intent` for blocked applications, denied access, policy
exceptions, site-health findings, site provisioning, external sharing,
retention, disposition, legal hold, identity risk, or compliance questions.
Use `list_employee_scenarios` for a governance gallery and
`open_employee_experience` for an exact scenario.

Show `selectedAgents` and public reasons, then display `phases` as evidence
collection, control evaluation, impact analysis, remediation planning, and
approval. Render `sections` as policy findings, access matrices, site-health
dashboards, document callouts, retention timelines, decision trees, and
approval panels. Preserve `sources` and `actions`.

Work IQ may find real policy documents, site-owner conversations, or approval
records when authorized. Keep them security-trimmed and source-specific. Never
claim the employee scenario inspected Purview, Entra, Defender,
SharePoint Admin Center, or a records system without a real connector response.

## Non-negotiable governance invariant

A blocked-access or enforcement experience must name the exact policy, affected
user or resource, evidence, unmet control, impact, required remediator, and
approver. Never silently enable a control, lower a threshold, bypass conditional
access, suppress a finding, or dispose of a record.

Call `prepare_employee_action` only for a returned action. Display
`actionClass`, the prepared or review-required state, submitted values, named
approver, and `executed=false`. The tool may prepare an exception request,
access-remediation draft, site-provisioning review, owner notification, or
records-disposition packet. It cannot grant access, provision a site, approve an
exception, change policy, close a finding, remove a legal hold, or delete data.

## Example scenarios

- "Why was this finance site blocked for me?" shows entitlement, policy,
  evidence, unmet control, resource owner, and a remediation draft.
- "Create a launch-team site with external guests and confidential files" shows
  template selection, sharing restrictions, retention, owner roles, and approval.
- "Which sites need attention this week?" renders a health dashboard, severity
  chart, owners, due dates, and policy-finding drill-through.
- "Can this project folder be deleted?" shows record class, retention trigger,
  legal-hold state, disposition date, and review boundary.
- "This app request violates policy; what are my options?" explains the exact
  enforcement rule and a compliant exception path without promising approval.

## Response contract

Lead with allowed, blocked, conditional, or insufficient evidence. Cite every
material control in `sources`. Show disagreements and stale evidence. Never
expose hidden reasoning, sensitive tool arguments, credentials, or unsupported
governance state.
