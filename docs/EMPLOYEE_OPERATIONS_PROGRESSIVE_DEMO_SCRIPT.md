# Agent IQ progressive employee-operations demo script

This script starts with one small personal answer and progressively adds
diagnosis, evidence, multiple agents, parallel work, forms, approvals, and
governance. It is designed for a 12-15 minute presentation, but Acts 1-4 also
work as a five-minute demo.

All people, systems, values, documents, and actions shown are synthetic demo
data. Actions prepare drafts or approval requests; they do not mutate an
enterprise system.

## 1. Start the demo

From the `agent-iq` directory:

```powershell
.\scripts\run_employee_showcase.ps1 -Mode preview
```

Open:

```text
http://127.0.0.1:8765
```

If the preview is already running, just open the URL. If port 8765 is occupied
by another process, use:

```powershell
.\scripts\run_employee_showcase.ps1 -Mode preview -Port 8766
```

The same prompts can be pasted into the **Agent IQ** Cowork/Microsoft 365
Copilot plugin. In the local browser preview, search for the expected scenario
title and open its card.

## 2. Complexity ladder

| Act | Intent | Main UX | Complexity |
| --- | --- | --- | --- |
| 1 | Leave balance | Number cards and compact coverage timeline | Personal answer |
| 2 | VPN failure | Urgent metrics, diagnostic timeline, decision tree | Two-agent diagnosis |
| 3 | Payslip change | Gross-to-net dashboard and document callouts | Grounded explanation |
| 4 | Expense report | Receipts, form, policy checklist, approval receipt | Governed action |
| 5 | Cancelled flight | Parallel travel and expense work, option comparison | Coordinated intent |
| 6 | Parental leave | HR, benefits, and payroll milestones | Life-event orchestration |
| 7 | New hire | Six-agent checklist, people cards, timeline | Cross-service command center |
| 8 | Blocked finance site | Health dashboard, policy findings, remediation approval | Governance finale |

## Act 1 - Start very simple: leave balance

**Prompt**

> Show my PTO balance and plan coverage for my vacation.

**Expected scenario:** **Time Off Coverage Planner**

**Show**

1. The **PTO balance: 6.5 days** number card.
2. The **4 of 4 tasks covered** card.
3. The compact coverage checklist and timeline.
4. The people cards showing who can cover the work.

**Say**

> "We start with the smallest useful experience. This is not a paragraph
> explaining leave. Agent IQ turns the answer into a glanceable card: the
> number I need, a compact visual of coverage, and the people affected."

Do not click the approval action yet. Keep this first moment fast and simple.

**Direct fallback**

```powershell
.\scripts\run_employee_showcase.ps1 -Mode scenario `
  -Scenario time-off-coverage
```

## Act 2 - Add diagnosis: VPN stopped after a network change

**Prompt**

> My VPN stopped working after I changed from office Wi-Fi to my phone
> hotspot. My customer meeting starts in ten minutes.

**Expected scenario:** **VPN Meeting Rescue**

**Show**

1. The urgent **time to meeting** card.
2. Agent formation for **IT Helpdesk** and **Identity & Security**.
3. The rescue timeline: network active, DNS failure identified, security block
   ruled out, guided fix in progress.
4. The decision tree and remaining fix step.
5. The cited IT runbook and security policy.

**Say**

> "The request contains a useful event: the network changed. IT diagnoses the
> connection path while Identity & Security checks whether policy caused the
> failure. The UI shows the public diagnostic timeline, not hidden
> chain-of-thought."

Click **Escalate to live IT support** only after showing the guided fix. Point
out that the result is a draft escalation and does not falsely claim that a
ticket was created.

**Direct fallback**

```powershell
.\scripts\run_employee_showcase.ps1 -Mode scenario `
  -Scenario vpn-meeting-rescue
```

## Act 3 - Add grounded explanation: why did net pay change?

**Prompt**

> Explain this month's payslip and why my net pay changed.

**Expected scenario:** **Payslip Explainer**

**Show**

1. The gross-to-net dashboard.
2. Document callouts beside the relevant payslip fields.
3. Payroll and benefits specialists contributing to one answer.
4. The source drawer.

**Say**

> "This is where the experience moves beyond a generic chatbot answer. The
> explanation is attached to the exact figures and backed by visible synthetic
> payroll and benefits sources."

Use a suggested follow-up such as:

> Compare this month with last month.

**Direct fallback**

```powershell
.\scripts\run_employee_showcase.ps1 -Mode scenario `
  -Scenario payslip-explainer
```

## Act 4 - Add a form and governed action: prepare an expense

**Prompt**

> Prepare my expense report from last week's client trip, attach the receipts,
> check completeness, and show me what still needs approval.

**Expected scenario:** **Expense Smart Submit**

**Show**

1. Receipt gallery and captured-total card.
2. The expense form.
3. The policy and completeness checklist.
4. Finance, Expense, and Travel specialists.
5. Sources behind the policy result.

Click **Submit expense report for approval**.

**Required result**

- status is **review required**;
- an approver is named;
- `executed` is **false**;
- the UI says no enterprise action was executed.

**Say**

> "Agent IQ can move from intent to an action-ready package without pretending
> it has written to the finance system. The approval boundary is part of the
> experience, not a footnote."

**Direct fallback**

```powershell
.\scripts\run_employee_showcase.ps1 -Mode scenario `
  -Scenario expense-smart-submit
```

## Act 5 - Add parallel work: cancelled flight plus hotel expense

**Prompt**

> My flight was cancelled and I need a hotel tonight. Compare the best
> rebooking options, check policy, prepare the expense, and draft the notices.

**Expected scenario:** **Travel Disruption Rebooking**

**Show**

1. Travel, Expense, and Task specialists forming from one request.
2. Parallel work in the observable execution phases.
3. The itinerary timeline.
4. Three alternate-flight options in the comparison table.
5. The hotel/expense form and missing-evidence callouts.

Click **Approve rebooking and send notices** and show that the output is still
an approval-bound draft.

**Say**

> "One employee sentence created two coordinated workstreams. Travel finds the
> recovery options while Expense checks the overnight policy, then Agent IQ
> presents one decision instead of two disconnected answers."

**Direct fallback**

```powershell
.\scripts\run_employee_showcase.ps1 -Mode scenario `
  -Scenario travel-disruption
```

## Act 6 - Add a life event: parental leave

**Prompt**

> Create a parental-leave plan across HR, Benefits, Payroll, and my manager.
> Show dates, pay milestones, coverage gaps, documents, and approvals.

**Expected scenario:** **Parental Leave Planner**

**Show**

1. HR, Benefits, and Payroll agent formation.
2. **16 weeks eligible** and **0 coverage-gap days**.
3. The leave form and milestone timeline.
4. Policy/document callouts.
5. The approval handoff.

**Say**

> "The complexity is now a life event rather than a single transaction. The
> system assembles policy, benefits, pay milestones, dates, and the manager
> handoff into one coherent plan."

Click **Submit leave plan for approval** and emphasize the named approver and
non-execution receipt.

## Act 7 - Add a six-agent command center: new employee

**Prompt**

> Set up a new employee's first week including laptop, payroll, access,
> benefits, training, and campus orientation.

**Expected scenario:** **New Hire Launchpad**

**Show**

1. Six specialists: Onboarding, HR, IT, Payroll, Campus Services, and Learning.
2. The cross-service checklist.
3. The first-week timeline.
4. People cards for key contacts.
5. **3 of 5 steps completed** and **1 day to full access**.

**Say**

> "This is the command-center pattern. Every service keeps its specialist
> responsibility, but the employee sees one plan, one status, and one set of
> next actions."

Use fullscreen mode here to show how the same experience expands from an inline
answer into a working surface.

## Act 8 - Governance finale: blocked finance-site access

**Prompt**

> Explain why my access to the finance site is blocked. Show the exact policy,
> affected resource, evidence, owner, approved remediation, and required
> approver. Do not bypass the control.

**Expected scenario:** **Site Health & Governance**

**Show**

1. The **68 / 100 site-health score**.
2. The **2 open policy findings**.
3. Site Health, Policy Governance, and Access Governance agents.
4. Exact policy findings, severity, evidence, owner, and due date.
5. The remediation checklist and approval panel.
6. The source drawer.

Click **Approve remediation plan**.

**Required result**

- the policy is not disabled or bypassed;
- the action is approval-bound;
- the receipt says no enterprise action was executed.

**Say**

> "The finale is not a cheerful success-shaped fallback. Agent IQ explains the
> block, identifies the unmet control, and prepares the approved remediation
> path while preserving the policy."

## 3. Close the demo

Return to the scenario gallery and say:

> "We progressed from one leave-balance card to coordinated HR, IT, Payroll,
> Finance, Expense, Travel, onboarding, and governance work. The same strict
> contract renders every experience, and every write-like action remains
> visible, approval-bound, and honest."

Then show:

- **30 scenarios**
- **26 agents**
- **30 synthetic knowledge sources**
- **16 reusable rich UI section types**
- **13 Cowork skills**

## 4. Presenter recovery guide

| Problem | Recovery |
| --- | --- |
| Browser did not open | Open `http://127.0.0.1:8765` manually. |
| Port 8765 is busy | The preview is probably already running; otherwise restart with `-Port 8766`. |
| Intent resolves unexpectedly | Search for and open the expected scenario title, or use the direct fallback command. |
| Fullscreen is unavailable | Continue inline; fullscreen is an enhancement, not a dependency. |
| Action asks for confirmation | Confirm only in the synthetic demo, then show the non-execution receipt. |
| A source link is unavailable | Open the source drawer and explain that the documents are synthetic demo sources. |
| Live Cowork plugin is unavailable | Use the deterministic local preview; it uses the same authoritative scenario contract. |

## 5. Non-negotiable demo language

Always say **prepared**, **drafted**, **review required**, or **not executed**.
Never claim that Agent IQ actually:

- submitted leave;
- changed payroll;
- repaired a network;
- created an IT ticket;
- booked travel;
- paid or submitted an expense;
- granted access;
- disabled a policy;
- remediated a SharePoint site.
