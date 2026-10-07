# Roadmap: from mechanisms to a swarm that finishes real problems

Written 2026-10-07, after the user asked how to move forward without stagnating. Order matters: competitiveness can only be studied once the framework behaves well, or the measurements say nothing. Each stage has an exit criterion; the next stage starts when it is met.

## What similar projects teach

- **The closest success is a swarm on a shared repository.** Anthropic's C compiler: 16 Claude agents, no orchestrator and no messages, coordinating only through git and lock files in `current_tasks/` (a blackboard by another name), built a 100,000-line C compiler that compiles Linux, over about 2,000 sessions and $20,000. What made it work: **a near-perfect external verifier** (established compiler test suites; "a loose verifier makes agents solve the wrong problem"), **failing tests as the unit of work** (with hundreds of independent failures each agent took a different one; when all hit the same bug, parallelism collapsed until an oracle split the work), **a regression ratchet** (CI blocking commits that break passing tests, added when fixes kept breaking old features), **short, greppable test output and progress files**, and **specialised roles added later** (deduplication, performance, design critique, documentation). ([Carlini, Anthropic 2026](https://anthropic.com/engineering/building-c-compiler))
- **Long-running builds need a separate evaluator and an explicit definition of done.** A planner expands the goal into a spec; generator and evaluator agree on testable criteria per sprint before building; a separate, tuned evaluator stops premature "done" (self-evaluation praised mediocre work; the evaluator itself had to be tuned by reading its logs). Structure was simplified again as models improved. ([Anthropic, harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps); [Anthropic, effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents): a feature list with a pass flag per feature, one feature per session, a progress file, a ratchet against editing tests)
- **Multi-agent failures are mostly organisational.** In MetaGPT, ChatDev and five other frameworks, failures come from specification and system design, misalignment between agents, and weak verification; requirements get diluted as they pass between agents, and the fix is to give every agent the original requirements. ([MAST, Cemri et al. 2025](https://arxiv.org/abs/2503.13657); [E2EDev 2025](https://arxiv.org/pdf/2510.14509)) Our fifo failure was exactly that: children saw only the planner's child tasks.
- **Blackboards for LLM agents are being revisited** and match or beat orchestrated designs on reasoning and data-discovery tasks; agents decide themselves whether they can contribute. ([Han and Zhang 2025](https://arxiv.org/html/2507.01701v1); [Google/UMass 2025](https://arxiv.org/abs/2510.01285)) No published evaluation on building software yet: AgentSwarm can be one.
- **Multi-agent costs about 15x the tokens** of a chat, worth it where the outcome's value is high, and weakest on tightly interdependent work such as coding. ([Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system))

What AgentSwarm already has from this list: a blackboard with claims (the lock files), git worktrees, independent acceptance tests the producer can't change, typed calls, spend tracking. What it lacks: **a real problem with an external verifier, failing tests as the unit of work, a regression ratchet on the level, the original goal given to every agent, and progress as a number.** Two days went into mechanisms tested on toy tasks; that is the stagnation to stop.

## Stage 1: one real problem, end to end (now)

**The problem:** a CommonMark (Markdown to HTML) converter in pure Python, from an empty seed, judged by the official spec's 652 examples. A held-out fifth of the examples is never shown to any agent, to tell real progress from fitting the visible ones.

**The loop, minimal:** the goal and the spec are on the board; work items are failing spec sections (the verifier decides the split, not a model guessing); builders claim an item, make its examples pass, and the gate merges only if no previously passing example breaks (the ratchet). Every agent sees the original goal next to its item. Progress is the pass rate, visible and logged per merge.

**Teams:** a planner that groups failing examples into items, builders, the gate. Acceptance tests are the spec examples themselves, which no agent writes.

**Cost control:** a first run capped at $5 to measure throughput (examples gained per dollar), then a budget proposal based on that number.

**Exit criterion:** unattended runs raise the pass rate steadily, without the ratchet ever being broken and without stalling; a clear picture of where it stops.

## Stage 2: several teams with different focus

Roles added one at a time, each kept only if it measurably helps: red teams on merged code (bugs found that the spec examples miss, checked against the held-out examples), a refactoring and deduplication team (code size and complexity over time), a reviewer of design, documentation. The C compiler added these after the core loop worked; so do we.

**Exit criterion:** each kept team has a measured contribution; the project reaches its finish line (a high pass rate, held-out examples included).

## Stage 3: dynamics

Models per role (routing, already built), local and hosted models, concurrency, cost per example gained. A second real problem of a different kind (for example a TOML parser, or a document project) to check that the stage 1-2 lessons generalise.

**Exit criterion:** the same framework finishes two different real problems, with per-role model choices backed by data.

## Stage 4: when is a swarm competitive?

The research question, studied once the framework behaves well: against a single big model given the same goal and the same verifier, at problem sizes from small to large, measuring solved share, cost and time. The user's expectation, to test: past some size, no model does it in one step, and a well-behaved swarm wins.

**The model axis (the user, 2026-10-07): one family, many sizes.** Qwen offers dense models from under 1B to 32B and mixture-of-experts models (for example 30B-A3B, 235B-A22B, Coder 480B-A35B), open weights, local and hosted. Within one family, size is the only variable, without differences in training data or style between providers; MoE adds a second axis, total parameters (knowledge) against active parameters per token (compute and cost), so the study can ask which of the two a swarm needs. The stage 3 probes start with the Qwen models this study will use.

## What changes now

- Inventory-suite experiments, routing probes and baseline comparisons pause; their results stay recorded (phase-8-goldilocks.md) and feed stage 3.
- The next work is stage 1: the CommonMark project setup, the ratchet, failing-test work items, and the original goal in every agent's context.

## Progress

**Stage 1, built (2026-10-07):** the ratchet check (`checks.<name>.ratchet: <level>`; `gating/ratchet.py`, `checks.RatchetCheck`): more of the level's failing tests pass and none that passed breaks, judged on the merge base, with tests the producer added not counting; the worklist (`worklist:` in a project; `control/worklist.py`): each test file with failing tests on the first shared level becomes an item, `max_open` at a time, `attempts_per_file` each; executors get a `run_tests` tool to see why single tests fail; `agentswarm import-commonmark` writes the seed (a stub `mdconv.to_html`, one protected test file per spec section) and the held-out fifth; `agentswarm score` gives the pass rate of the level, held-out tests included. The project is `examples/commonmark/project.yaml`: Haiku builds, the ratchet gates, a $5 daily budget for the first run. Waiting for: the spec's examples (spec.json from spec.commonmark.org), to be downloaded with the user's permission.
