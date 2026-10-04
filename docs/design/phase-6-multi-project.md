# Phase 6: Several projects, one scheduler

Status: **complete** (decided on the recommendations). Proposal sections: §14.2 (scheduler), §14.1
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

**Step 4 (swarm).** `swarm.py`: a swarm file lists project configs and roots (relative to it, `~`
expanded) and scheduler limits; `Swarm.open` builds each project with the shared scheduler in its
services; `Swarm.run` runs every project's loop as an asyncio task (`gather`), reporting a project still
busy at the round limit instead of failing the others. `Project.step` runs gates, the router and the
supervisor with `asyncio.to_thread`, so a project's blocking work doesn't stall the others; SQLite
connections allow use from worker threads (a project's steps still run one at a time). `agentswarm swarm
SWARM [--task PROJECT=TEXT]...`; `examples/swarm-quickstart.yaml` runs two quickstart projects offline.
- **Bug found by the end-to-end test:** the budget was checked once per runner step, so a step could
  spend past it on every waiting artifact. It is now checked before every action.
- **The exit criterion holds in tests** (`tests/test_swarm.py`): two projects run concurrently (model runs
  overlap), the one with room finishes, the one with a $2 budget stops at exactly $2 and reports it once.

**Step 5 (live run, 2026-10-04).** `examples/swarm-live.yaml`: the greeter (software preset, Jev in
shadow) and the handbook (document preset, Jev active for sizing) in one process, sharing a scheduler
(`max_model_calls: 2`, `max_sandboxes: 2`), gemma4:e4b through Ollama, Docker sandboxes.
- **Both projects progressed at the same time.** `status`, read during the run, showed the greeter at
  experimental while the handbook was at final.
- **Both stopped at their human gates:**
  - the greeter at candidate, after the integration tester and red team signed off (9 model runs);
  - the handbook at final, after its three reviewers (12 model runs; the same two minor findings as the
    solo run, since the model runs at temperature 0).
- **Spend was $0.00:** local models, unpriced and reported as such.
- **The exit criterion, "two projects run concurrently within their budgets", holds live as well as in
  the offline test** (which also covers a budget stop and a failing project).
