---
name: vacation-balance
description: Check personal vacation, PTO, holiday, or annual-leave balances, and clarify manager team scope before answering team leave questions.
license: MIT
metadata:
  author: Agent IQ
  version: "1.2.0"
---

# Vacation balance

## Outcome

Resolve who the request is about before retrieving leave information. For a
personal balance, return one compact Apple-Watch-style ring with the remaining
balance, one supporting line, the selected Vacation & Leave Agent, and one
source. For a manager request, establish the authorized team and geography
before searching.

## Trigger phrases

Use this skill for requests such as:

- "How much vacation do I have remaining this year?"
- "How many PTO days do I have left?"
- "Show my annual leave balance."
- "Vacation days left."
- "How much leave does my team have?"
- "Show team time-off coverage in Europe and APAC."

Do not ask the user to choose an agent. Do not open the Employee Operations
gallery for a vacation request.

## Intent validation before any tool

Resolve only the dimensions that materially change the answer:

- **Audience:** the user or a team.
- **Request type:** remaining balance, a manager overview, team availability, or
  leave planning.
- **Period:** current year unless the user names another period.
- **Team geography:** United States, Europe, APAC, or all regions.

Use these rules:

1. If the user says "my", "I", or "me" and asks for a balance, treat it as a
   personal request and do not ask a redundant role question.
2. If the user explicitly says "my team", "direct reports", or names an
   organization, treat it as a manager request.
3. For an ambiguous request such as "vacation days left", ask exactly:
   **"Is this for you or your team?"** Then wait. Do not call a tool in the same
   turn.
4. If the user chooses a team and manager authority is not established by the
   conversation or host context, ask whether they are checking a team they
   manage. Do not expose anyone else's leave data without that scope.
5. For a team request with no geography, ask exactly:
   **"Which team scope should I use: United States, Europe, APAC, or all
   regions?"** Then wait.
6. Ask at most one focused clarification per turn. Never ask for information the
   user already supplied.

## Personal balance workflow

1. Call `check_vacation_balance` exactly once after the audience is clearly
   personal.
2. Pass the user's concise balance question as `question`. If they only answered
   the clarification with "me", use "How much vacation do I have remaining this
   year?"
3. Do not call `search_agents` first. The dedicated tool already performs
   catalog discovery, ranking, exact selection, native A2A invocation, and
   source validation.
4. Do not call `orchestrate`, the single-agent formulation tool,
   `open_employee_experience`, or another employee scenario tool before or
   after it.
5. Do not call Work IQ for a plain balance request. Use Work IQ only when the
   user explicitly refers to a meeting, email, file, or named decision that is
   needed for a separate planning request.
6. Do not call `get_execution`, inspect files, or reconstruct the result. Use
   audit tools only when the user explicitly asks for the execution record.

The expected selected agent is `employee-vacation-leave`. The tool must expose
that agent in `selectedAgents` and exactly one item in `sources`.

## Manager and team workflow

1. Complete the audience, manager-authority, and region clarifications first.
2. Call `orchestrate` once with the exact manager intent, requested period, and
   region written into the intent. Request aggregate team information by
   default; include individual names only when the user explicitly asks and the
   returned policy/evidence permits it.
3. Accept the result only when its evidence explicitly supports the requested
   team and region. If team-scoped evidence is unavailable, say that plainly.
4. Never substitute the user's personal balance for a team answer, and never
   merge United States, Europe, APAC, or all-regions results without saying
   which scope was used.
5. Keep the result concise. Do not open the Employee Operations dashboard.

## Compact personal response contract

Treat the MCP App as the primary answer. Its `presentationStyle` is
`compact_ring`, and it renders only:

- one small set of concentric vacation rings;
- the title, normally "12 vacation days remaining";
- one supporting line, normally "10 taken | 3 booked | 25 total";
- one compact Vacation & Leave Agent chip;
- one numbered source chip, `[1]`.

Outside the MCP App, add no text unless the host needs a fallback. The fallback
is one sentence:

> You have 12 vacation days remaining: 10 taken and 3 booked from a
> 25-day entitlement. [1]

Preserve the returned values and citation. Do not append a caveat, environment
label, implementation note, or second summary.

## What must stay out of the response

Do not expand the result. Do not request fullscreen or side-by-side display.
Do not show a dashboard, command center, metric grid, table, timeline,
recommendation panel, action dock, workflow stage rail, selected-squad drawer,
scenario gallery, or follow-up buttons. Do not repeat inventory counts,
transport diagnostics, protocol narration, internal lifecycle events, thought
process, routing deliberation, tool arguments, or evidence excerpts.

The compact answer may name the selected agent, but it must not turn that agent
into a large card. The small agent chip is the complete Agent Card treatment
for this intent.

Do not volunteer the August leave recommendation, dated leave entries, or
prior-year pattern. Those are not needed to answer a remaining-balance
question. If the user later asks a planning question, handle it as a separate
intent rather than expanding this result automatically.

## Trust and evidence

- entitlement: 25 days;
- taken: 10 days;
- booked: 3 days;
- remaining: 12 days;
- selected agent: `employee-vacation-leave`;
- source count: exactly one.

The values must reconcile: taken + booked + remaining equals entitlement. The
source is authenticated and invocation-bound. Preserve its title and safe URL
as citation `[1]`; never fabricate a source or copy raw marked JSON into the
conversation.

## Failure behavior

- If `presentationStyle` is not `compact_ring`, do not present success.
- If `selectedAgents` does not contain exactly
  `employee-vacation-leave`, surface the selection-contract failure.
- If `sources` does not contain exactly one authenticated source, say grounded
  evidence was unavailable.
- If native A2A, source validation, or balance reconciliation fails, surface
  the returned error plainly.
- Never replace a failed call with a canned balance, an old Employee
  Operations card, or a model-generated estimate.
- Never reveal private chain-of-thought or connector credentials.

## Why this answer

If the user asks why this result is trustworthy, answer in one line from the
returned public fields:

> Agent IQ selected the Vacation & Leave Agent, invoked it over native A2A, and
> validated the cited source.

Do not rerun the tool unless the user asks for a fresh balance.
