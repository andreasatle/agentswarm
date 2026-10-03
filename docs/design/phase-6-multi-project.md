# Phase 6: Several projects, one scheduler

Status: **in progress** (decided on the recommendations). Proposal sections: §14.2 (scheduler), §14.1
(budgets), §16 (project isolation).

**Exit criterion (§19):** two projects run concurrently within their budgets.

## Where we start

- One process runs one project; its teams take turns in one loop.
- Nothing measures cost: `budgets.usd_per_day` is in the config but unused (backlog).
- Model calls, Jev requests and sandbox runs are unlimited.

## Design

### Spend is recorded on the board

Every model call reports its token usage (LangChain gives `usage_metadata` per reply; Jev answers with
`usage`). Workers and deciders return it with their result, and the team appends a `Spent(team, model,
input_tokens, output_tokens, usd)` event. Prices come from config (`models.<name>.usd_per_million:
{input, output}`, `jev.usd_per_million`); a model without a price costs 0 and says so in reports. Because
spend is on the board, it survives restarts, replays, and shows up per artifact.

### The scheduler (§14.2)

One `Scheduler` per process, shared by every project, holds the resources:

| Resource | Limit (config) | Granted per |
| --- | --- | --- |
| concurrent model calls | `max_model_calls` | team action |
| Jev requests | `jev_per_minute` (about 1,200) | request |
| sandbox runs | `max_sandboxes` | check run |
| spend | each project's `budgets.usd_per_day` | team action |

A team asks for a grant before it acts (`async with scheduler.grant(project, team)`). Fairness: waiting
projects are served round-robin, so a busy project can't starve a quiet one. A project over its daily
budget gets no grants: its supervisor escalates once ("budget exhausted") and the project idles until the
next day or a person raises the budget. The scheduler makes no judgements about content (§14.2).

### Running several projects

`agentswarm swarm SWARM.yaml` reads a list of projects (config path and root each) and scheduler limits,
and runs each project's loop as its own asyncio task. Blocking work (git, sandboxes, synchronous Jev
calls) runs in worker threads so projects really overlap; each project keeps its own board, decision log,
repository and worktrees (§16, isolation).

## Steps

1. Usage from workers and deciders; prices in config; the `Spent` event and spend views.
2. `Scheduler` with grants, round-robin fairness, Jev rate limiting and sandbox slots.
3. Budgets enforced through grants; the supervisor's "budget exhausted" escalation.
4. `Swarm`: several projects as concurrent tasks, blocking work in threads; `agentswarm swarm`.
5. End to end with fakes: two projects run concurrently, one hits its budget and stops, the other
   finishes; then a live run with the greeter and handbook projects.

## Decisions made during implementation

**Step 1 (spend on the board).** Workers keep returning plain results: a `Meter` callback, given to each
team's worker when the catalog builds it, hears the token usage of every run (LangChain's
`usage_metadata`, summed over the agent's messages, plus the final-answer call, now made with
`include_raw` to see its usage). `SpendLedger` prices usage with `models.<name>.usd_per_million` and
appends `Spent(by, model, tokens, usd, priced)`; unpriced models record 0 and `priced: false`; calls with
no tokens record nothing. Jev answers carry their usage (`Answered.usage`), recorded as `jev:<point>`
with `jev.usd_per_million`; the LLM fallback meters as `jev-fallback`. `BoardViews.spent_since(t)` sums
spend for budgets. The ledger writes the board directly, like the decision log: it is infrastructure,
not a team.

**Steps 2–3 (scheduler, budgets).** `control/scheduler.py:Scheduler` holds an asyncio semaphore for model
calls (team actions), a thread-safe sliding-window `RateLimiter` for Jev (the `JevDecider` waits on it before
each request) and a bounded semaphore for sandbox runs (`ThrottledSandbox`). Fairness without a dispatcher:
each project's loop waits for at most one grant at a time and the semaphore serves waiters in arrival
order. `control/budget.py:Budget` reads spend for the current UTC day from the board; an exhausted
project's team runners skip their turn (nothing marked handled, as with a pause), and the supervisor
escalates once a day (project-wide, `artifact: None`). Gates keep judging over budget: Jev is cheap, and
the model spend that budgets exist for is in team actions. Budgets work without a scheduler too.
