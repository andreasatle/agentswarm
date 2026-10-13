# Changelog

What changed in AgentSwarm, latest first. Each entry says the problem that called for it, what was built, and what it means for the swarm.

## 2026-10-13: Self-supervised: YAML 1.2 without labels, 97%

- **Problem:** TOML is familiar to the models; would self-supervision hold on an intricate spec, with even the spec's examples removed?
- **Solution:** the same setup: derived checks from a check writer of another model family, disputes ruled by a referee reasoning first, all 681 official tests hidden.
- **Implication:** 663 of 681 hidden tests, for $4.60: the same 97% the supervised run reached on its held-out tests, at about 2.7 times its cost. The referee upheld two wrong checks and kept one right one.

## 2026-10-13: Self-supervised: TOML without labels, 100%

- **Problem:** every problem so far came with tests that give the right answers. Most real work doesn't.
- **Solution:** a check writer on another model family writes checks from the spec that need no known answers (round trips, equivalent inputs, rules the spec states); the builder may dispute a check, quoting the spec; a referee of a third family rules, reasoning first, by majority of three. The official tests stay hidden and only score. A first run failed and showed why (an interface the goal didn't state, a referee whose verdicts contradicted its reasons); both were fixed.
- **Implication:** all 679 hidden TOML tests pass, for $3.43, against $2.44 with labels. One check compared dates with Python's own TOML parser, a hidden label, now banned; and TOML is familiar to these models, so a less familiar problem is the next test.

## 2026-10-12: HTML5: 100% visible, 99.7% held out

- **Problem:** the hardest problem so far: the HTML standard's parsing algorithm, which must build the same tree as a browser for any input, broken markup included. Judged by the 1,958 tree-construction tests the browsers maintain.
- **Solution:** the same setup as YAML: the standard's parsing chapter as text, with its examples and worked cases left out, every fifth test held out, and the instruction to follow the standard's algorithms rather than rules guessed from tests. On the way, a test-counting bug was fixed: very long test output was cut in the middle and some tests went uncounted; now the count refuses cut output.
- **Implication:** all 1,593 visible and 364 of 365 held-out tests, for $5.18: an HTML parser in about 1,400 lines. Seen and unseen agree within a point.

## 2026-10-12: YAML 1.2: 100% visible, 97% held out

- **Problem:** jq showed builders writing only what the visible tests demand. YAML, with a notoriously intricate spec, was the next test of that.
- **Solution:** the official yaml-test-suite (402 cases, every fifth held out), the spec given to the agents without its examples, and one added instruction: hidden tests check what the visible ones don't, so implement each rule as the spec states it.
- **Implication:** 563 of 563 visible and 115 of 118 held-out tests in one run, for $1.69: a complete YAML 1.2 parser in 901 lines. The gap between seen and unseen shrank from 13 points to 3.

## 2026-10-12: jq, a language: 100% visible, 87% held out

- **Problem:** the first two problems were formats; a harder test was an interpreter for a programming language, jq, judged by jq's own 675 tests (every fifth held out, all checked against the real jq first).
- **Solution:** the same framework with a new config and importer; the input reaches the interpreter as JSON text, as jq reads it.
- **Implication:** 550 of 550 visible tests in one run, for $4.14, but 109 of 125 held out. The gap is the first sign of fitting the visible tests: no special cases, but functions the manual documents and no visible test calls were never written. Next problems will get work items from the spec as well as from the tests.

## 2026-10-12: CommonMark done, 100%

- **Problem:** the Markdown converter stood at 89%; what was left was mostly link reference definitions, which the links and images depend on.
- **Solution:** back in the ordinary worklist loop: one work item per spec section with failing examples, each tried whole by the cheapest model expected to succeed.
- **Implication:** three items, each merged on its first attempt, took the converter to 522 of 522 visible examples and 130 of 130 held out, for $0.49. Both real problems so far are finished against their full definition of done.

## 2026-10-12: a stopped sandbox stops the run

- **Problem:** with the container engine not running, the tests could not run at all, and the run read that as "0 of 0 tests" and finished quietly.
- **Solution:** the sandbox asks the container engine once before its first command and stops with a clear message if it doesn't answer.
- **Implication:** a broken environment can never look like failing work, so it can't mislead the model router or waste a run.

## 2026-10-11: split steps are building blocks; try before splitting

- **Problem:** the planner split one work item three levels deep, into 53 tiny steps, before any model had tried it. Each tiny step was required to make a test pass on its own, which a building block often can't, so all of them failed.
- **Solution:** only the step that completes a task must make a test pass; the steps before it must only break nothing. Work items are tried whole first and split only after every model has failed them (ADaPT, Prasad et al. 2024). A new command retires a task and all its steps.
- **Implication:** decomposition is used where it is needed, not by default, and a plan's steps are judged by what each can deliver.

## 2026-10-11: TOML, the second problem, 100% on its first run

- **Problem:** one solved problem could be a fit to that problem; the framework had to be shown working on another one with nothing but configuration.
- **Solution:** a TOML 1.0.0 parser from an empty stub, judged by the official toml-test suite (679 files, every fifth held out), set up with a config and a seed importer only.
- **Implication:** 555 of 555 visible and 124 of 124 held-out tests pass, for $2.44. Setting up a new well-specified problem is now an afternoon's configuration.

## 2026-10-11: no model call for a step that is already done

- **Problem:** after one big step solved most of TOML, later steps still called models to "fix" work that was already passing.
- **Solution:** the executor runs the checks first; a step whose goal is already met is closed without any model call.
- **Implication:** the swarm stops paying for work that earlier work already did.

## 2026-10-11: a live web monitor

- **Problem:** following a run meant reading text reports.
- **Solution:** `agentswarm monitor` serves a read-only page on localhost: every project, its teams, models, spend, the plan as a tree, and each step's history, rebuilt from the event log on every request.
- **Implication:** a run can be watched as it happens, across several projects at once.

## 2026-10-11: keep unfinished work; protect only the independent tests

- **Problem:** an attempt that made real progress but didn't finish was thrown away. And a 99%-complete final switch-over was rejected because seven of the builder's own temporary tests failed.
- **Solution:** passing but unfinished work stays on the branch, and the next attempt is told what is left. The no-regressions rule now guards only the acceptance tests the builder can't change; its own tests it may rewrite or delete, never leave failing.
- **Implication:** progress accumulates across attempts and models instead of restarting, and the guard protects what matters without blocking good work.

## 2026-10-10/11: the restructuring, from 43% to 89%

- **Problem:** the converter's first design couldn't nest lists and block quotes; patching section by section had stalled.
- **Solution:** a planned restructuring in 19 steps, one block type at a time, the way the spec's own parsing strategy describes it, with every passing example kept passing at each step.
- **Implication:** 89% visible and held out, for $13.92. The swarm can carry out a large, ordered change, not only small fixes.

## 2026-10-10: choosing models by expected cost per success

- **Problem:** models were tried in a hand-written order, which ignored what the swarm had learned about them.
- **Solution:** every attempt's cost and outcome go into a shared ledger. A router (Thompson sampling) ranks models by expected cost per success, among those likely enough to succeed. After a failure, it picks again among the models not yet tried on that step, and spending on unproven expensive models is capped.
- **Implication:** the cheapest model that does the job gets the work, and the choice improves with every task in every project.

## 2026-10-10: split on failure

- **Problem:** a step every model failed went to a person.
- **Solution:** it goes back to the planner with the reason it failed, and is split into smaller steps (ADaPT); only past a depth limit does it reach a person.
- **Implication:** hard steps are broken down automatically, and a person is asked only when splitting no longer helps.

## 2026-10-10: every run is scored

- **Problem:** whether a run was worth its money was judged by reading logs.
- **Solution:** every run measures the visible and held-out pass rates at its start and end, and reports the cost per example gained.
- **Implication:** each run's value is a number, and overfitting would show as a gap between the two rates.

## 2026-10-10: leaner context, provider-neutral

- **Problem:** long agent sessions paid again and again for old tool output.
- **Solution:** old tool results are cleared when each model's own prices say it pays (the calls left, the cached and full input prices), or when the model's context window needs it; typed answers go through tool calls with retries, on any provider.
- **Implication:** lower cost per attempt, with the same behaviour on every model provider.

## 2026-10-10: a best-practice review of every mechanism

- **Problem:** several mechanisms had grown from experiments without being checked against the literature.
- **Solution:** each one was held against published evidence; six fixes followed, including removing a read limit that stopped a quarter of the attempts that would have succeeded.
- **Implication:** every mechanism now has its reason and its source written down (docs/design/best-practice-review.md).

## 2026-10-08/09: builders that work like developers

- **Problem:** builders spent most of their tokens rereading whole files, rewriting them, and reading long test output.
- **Solution:** builders get a map of the repository, read in parts, search, edit by replacement, get short test output, and see how many calls they have left.
- **Implication:** far fewer tokens per change; a smaller model can now do work that needed a large one.

## 2026-10-08: accurate costs and spending limits

- **Problem:** cost estimates from price lists drifted from the bills, and nothing stopped one run from spending too much.
- **Solution:** spend records what the provider actually charged when it says so, and cached input is priced separately. Spending limits per day and per account hold across every run on the machine, with a reserve kept back.
- **Implication:** costs are trustworthy, and no run can overspend.

## 2026-10-07: the first real problem, end to end

- **Problem:** test suites of small tasks couldn't show whether a swarm finishes real work.
- **Solution:** the CommonMark spec as a real problem: a work list of failing spec sections, a gate that merges a change only if no passing example breaks (the ratchet), a fifth of the examples held out from every agent, and builders that repair failing work instead of starting over.
- **Implication:** the swarm is judged against an objective definition of done, as a real project would be.

## 2026-10-06/07: tests the producer can't change

- **Problem:** a builder that writes its own tests can make them pass without solving the task.
- **Solution:** an acceptance team writes the tests from the task before any work starts; the producer can't change them, and the steps of a split task inherit them.
- **Implication:** in the experiment, the cheap builder working against these tests solved 4 of 5 of the hardest tasks instead of 2 of 5.

## 2026-10-03/05: the framework

- **Problem:** the starting point: agents working toward a goal without a central controller.
- **Solution:** a blackboard (an append-only event log) that loosely coupled teams read and write; work moves through levels behind gates and hard checks; judging and routing questions go to Jev; document projects as a second domain; several projects at once; an independent audit and the hardening that followed it.
- **Implication:** every decision and every cent is on the record, a crash loses nothing, and teams can be added without rewiring the rest.
