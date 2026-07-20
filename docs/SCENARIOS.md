# Agent IQ scenarios

These prompts exercise dynamic formation rather than fixed workflow routing.
Use synthetic fixture results only as a product demonstration.

## Local showcase

```powershell
pwsh .\scripts\agent-iq.ps1 demo
```

The local preview is credential-free and does not invoke Azure, Microsoft 365,
SAP, payroll, HR, or device-management systems.

## Live Cowork scenarios

### VPN recovery

```text
My VPN is down.
```

Expected:

- one generic orchestration call;
- exactly `employee-it-helpdesk` selected;
- one read-only, source-backed VPN recovery flow;
- reconnect first, then the explicit escalation boundary; and
- no claim that Agent IQ diagnosed the device or completed a repair.

The compact visual is a Sankey-inspired recovery flow with a vertical mobile
fallback.

### Vacation balance

```text
How much vacation do I have remaining this year?
```

Expected:

- exactly `employee-vacation-leave`;
- 12 remaining days from the labelled synthetic ledger;
- entitlement, taken, booked, and remaining shown as one compact radial view;
- one governed source; and
- no leave request created.

### Product formulation

```text
Which enzymes power BrightWash Cold cleaning?
```

Expected:

- exactly the formulation specialist;
- protease, amylase, mannanase, and lipase;
- only intent-relevant formulation evidence; and
- no unrequested competitor comparison.

This branded sample is test data. It does not imply affiliation, endorsement,
or access to a live product system.

### Atomic employee skills

Use these to inspect one domain at a time:

```text
Ask HR what the parental leave policy covers.
Ask IT how to recover remote access.
Ask Expense what evidence is needed for a client meal.
Ask Payroll when the next payslip is available.
```

Each prompt should select the smallest eligible squad, normally one agent.

### Dynamic multi-agent formation

```text
My VPN is down and I need to check the status of an expense reimbursement.
```

Expected:

- the model decomposes two independent work items;
- trusted discovery selects IT and Expense independently;
- both run through the same generic orchestration path;
- independent work can run concurrently; and
- one synthesis preserves each source and any partial failure.

There is no composite rescue skill or manually authored agent pair.

## What to look for

High-quality runs show:

1. a small number of readable progress stages;
2. the selected agent or squad with concise public reasons;
3. an honest answer, partial result, or decline;
4. source and agent provenance;
5. no raw tool arguments, internal event codes, or hidden reasoning; and
6. no large card application for a simple one-agent request.

## Negative checks

The VPN result must not invent:

- DNS or gateway root cause;
- an outage;
- device telemetry;
- a completed reconnect, restart, reset, or repair; or
- metrics not present in the source record.

The mixed scenario must not:

- route through a fixed scenario skill;
- erase one successful result when another agent fails;
- merge sources without claim provenance; or
- claim a write or approval occurred.

## Report a failed scenario

Capture only:

- the sanitized user prompt;
- package version;
- execution ID;
- selected agent IDs;
- visible status and error code; and
- whether the conversation was fresh or reopened.

Do not attach tokens, tenant content, raw prompts sent to agents, or source
bodies containing sensitive information.
