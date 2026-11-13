# How the swarm works: a reading of the code

Written 2026-11-13 from the code alone (`src/agentswarm/`), not from the design documents. Where the code departs from common practice, a note says so. File references are to `src/agentswarm/`.

## 1. The one idea: teams never talk to each other

Everything in the swarm meets on one **blackboard**: an append-only event log in SQLite (`board/eventlog.py`, one `blackboard.sqlite` per project). No team calls another team, and no central program tells a team what to do. A team reads the board, does some work, and appends events. Other teams notice those events on their next look and react.

The log is the only truth. Everything else is a **view** computed from it: `BoardViews` in `board/blackboard.py` is a fold over the events, one `match` case per event type. It knows which artifacts exist and at which level, which findings are open, who holds a claim, what was spent, whether the project is paused, and so on. `Blackboard.current()` catches the views up with any new events before every read, so every team always sees the same state, and a crashed run can be resumed by reading the log again.

The events are typed Pydantic models in `core/domain.py` (about 40 kinds: `ArtifactCreated`, `Claimed`, `CommitPushed`, `FindingAdded`, `Promoted`, `Rejected`, `Escalated`, `Spent`, `Attacked`, and so on). The fold ends with `assert_never`, so a new event type the views don't handle is a type error.

## 2. What the work is made of

- **Artifact:** one unit of work: a task in words, a git branch, a level, and optionally a parent (it was split from, or fixes something on, another artifact) (`Artifact` in `core/domain.py`). A document section is an artifact; a fix to a section is another.
- **Level:** how mature an artifact is. A project has an ordered ladder of levels (`LevelLadder` in `core/config.py`); the document runs use two, `draft` and `reviewed`. The lowest level is a short-lived branch per artifact (`raw/a-0006-…`); every higher level is one long-lived branch shared by all artifacts that reached it. Moving up means merging into that branch.
- **Finding:** a problem someone reported on an artifact, with a kind, a severity and a detail. Findings are how critics hand work to builders.
- **Claim:** a lease (10 minutes by default) that gives one team at a time the right to work on an artifact.
- **Gate:** the rule for moving from one level to the next: hard checks, then optionally a judge.

The code is in the repository too: each project has a git repository (`workspace/git.py`), and every team works in a throwaway worktree checked out for one action.

## 3. The loop every team runs

A team is anything with four members (`Team` protocol in `teams/runner.py`): a `name`, the level it `watches`, a cheap `relevant(ctx)` question ("is there something for me here?") and an expensive `act(ctx, worktree)` that does the work and *returns* events. A team never writes the board itself.

`TeamRunner` drives one team. Each step:

1. **Find candidates:** every live artifact at the team's level whose current version this team hasn't handled yet, that nobody holds a claim on, and that isn't waiting for a person.
2. **Ask `relevant`.** If the answer is not yes, the version is marked handled, so the team doesn't ask again until the artifact changes.
3. **Claim it** (`board/claims.py`). The claim is appended with *optimistic concurrency*: only if the log still ends where the registry last looked, so two claimants can't both win.
4. **Check out a worktree** and call `act`, inside a `SpendScope` that books every model call's cost against this artifact and refuses calls that would break the budget.
5. **Publish, fenced:** the returned events are appended only if no newer claim exists and the artifact hasn't changed since the claim was taken. Otherwise the work is discarded.
6. **Release the claim** and remember the new version as handled, so a team never reacts to its own output.

This is *level-triggered*: a team doesn't receive messages, it looks at the current state each step and acts on whatever needs it. Missing a step costs nothing; the state is still there next time.

**The project's step** (`Project.step` in `project.py`) runs, in order:

1. the specifier or **briefer** (until it is done, nothing else runs);
2. every team runner;
3. the campaigns (red teams, reviewers), which only attack once the builders are idle;
4. then the plain-code parts: crash recovery of level branches, the **promoters** (one per gate), the **finding router**, the **worklist**, the **supervisor** and the **outcome recorder**.

`run_until_idle` repeats this until a whole step takes no action.

**Concurrency** (changed 2026-11-13, after this was first written): each team now works on up to `parallel` artifacts at a time (default 4, `TeamConfig.parallel`), so a writer writes several sections at once. Only the waiting overlaps: the model calls. Git and the checks are synchronous, so they never interleave, and each action keeps its own claim, worktree and spend scope. The budget counts every model run in flight at its worst case until its spend is recorded (`Budget.admit`), so parallel calls can't jointly pass the cap. The teams themselves still take turns within a project's step, and the review campaign still attacks one part at a time. Before this change nothing in a project ran at the same time; the claims and fenced publishes were written for concurrency the code didn't use.

## 4. The kinds of agents

"Agent" covers two quite different things in this code: **teams** that call models, and **controllers** that are plain code. Teams are built from the project's YAML by name through the catalog (`TeamCatalog.standard()` in `teams/catalog.py`).

**Teams that produce (they commit to an artifact's branch):**

| `use:` | class | what it does |
|---|---|---|
| `executor` | `ExecutorTeam` (`teams/executor.py`) | builds a task: a LangChain agent with file tools (list, read, search, edit, write, run checks, run tests) loops until it gives a typed report |
| `stateless_builder` | `StatelessBuilder` (`teams/stateless.py`) | the same job, but plain code runs the loop: each round the model gets the whole current state and returns one typed answer (edits plus what to read or run next); its memory is only its own `notes` |
| `writer` | `Writer` (`teams/documents.py`) | an executor for documents: for a section of the outline it reads the section's sources itself, in plain code, and asks the model for the section in **one** typed answer, with one forced rewrite if it runs more than 15% over its word budget |

**Teams that plan:**

| `use:` | class | what it does |
|---|---|---|
| `decomposer` | `Decomposer` (`teams/decomposer.py`) | asks the judge (Jev) whether a new task fits one branch; if clearly not, a model proposes child tasks and the artifact becomes an *umbrella* over them |
| `outliner` | `Outliner` | the decomposer with document instructions |
| (the briefer) | `Briefer` (`teams/specifier.py`) | not in the catalog; it runs first. It reads the sources and drafts the brief and outline, one artifact per section, each with its sources and a word share (`SectionAssigned`) |
| (the specifier) | `Specifier` | the software counterpart: drafts a spec from the goal before anything is built |

**Teams that check (they never commit; they report findings):**

| `use:` | class | what it does |
|---|---|---|
| `acceptance_tester` | `AcceptanceTester` | writes tests for a task *before* it is built; the builder may not change them (the worktree restores them after every attempt) |
| `red_team`, `integration_tester`, `edge_value_tester`, `text_tester`, `error_tester`, `contract_tester` | `ProbingAnalyzer` subclasses (`teams/analyzer.py`) | examine merged work and may only report a problem backed by a test they wrote that fails; unbacked claims are dropped |
| `fact_checker`, `consistency_checker`, `audience_reviewer` | `ReviewingAnalyzer` subclasses | the document version: the evidence is a verbatim quote, which plain code checks is really in the file |

**Campaigns** (`teams/campaign.py`) are critics run by plain code in rounds over the merged product, part by part and focus by focus, until a set number of rounds find nothing new. `Hardener` (`teams/hardening.py`) runs red teams against a spec; `ReviewCampaign` (`teams/review.py`) runs reviewers against a document's sections. Their confirmed findings become new artifacts (tasks for the builders).

**The referee** (`teams/referee.py`) is the arbiter. When a builder disputes an acceptance test, quoting the spec, or a reviewer's finding needs a ruling, a model of another family than the producer reads the evidence and rules. Plain code checks the quote first, so a made-up quote never reaches the referee.

**Controllers (plain code, no model):**

- **Promoter and gate** (`gating/promotion.py`, `gating/gate.py`): for each artifact with a commit no gate has ruled on, run the hard checks; if they pass, ask the judge if there is one; then promote, reject (the findings replace the open ones) or escalate to a person.
- **Merge queue** (`gating/merge_queue.py`): promotions into a level are serialised; each is merged onto the level's tip in a scratch worktree and the checks are run *again on the merged result* before the branch moves. A level branch never holds an unchecked merge.
- **Finding router** (`control/router.py`): every open major or blocker finding on an artifact above the lowest level becomes a *fix artifact* at the lowest level, once; the finding is resolved when the fix reaches its level.
- **Worklist** (`control/worklist.py`): for a project built against an external test suite, each test file with failing tests becomes a work item.
- **Supervisor** (`control/supervisor.py`): escalates stalled artifacts, too many rejections in a row, or too many commits; every so often asks the judge whether recent work still serves the goal, and pauses new work if it clearly doesn't.
- **Outcome recorder** (`control/outcomes.py`): decides afterwards whether each routed model call succeeded, from later events (did its commit get promoted?), and writes that to the routing ledger. The team that made a call never grades it.
- **Budget and scheduler** (`control/budget.py`, `control/scheduler.py`): refuse calls over the day's limit, and share model-call slots between projects.

## 5. How work is delegated

Nobody hands work to anybody. Work moves because one part leaves something on the board that another part's `relevant` says yes to. The hand-offs, all through events:

1. **A task appears** as an `ArtifactCreated`: made by a person (`agentswarm` CLI), by the briefer (one per section), by the worklist (one per failing test file), by the finding router (one per blocking finding), or by a campaign (one per confirmed attack).
2. **A planner sizes it**, if the project has one. The executor's `relevant` returns no until a `Sized` event exists. If the task doesn't fit, the artifact becomes an umbrella and its children are created; they are built in order, each from what the earlier ones merged (`waits_for`). A *join* gate (`gating/join.py`) promotes the umbrella when its children are through.
3. **An acceptance tester writes its tests**, if the project has one; the executor waits for `AcceptanceWritten`.
4. **An executor builds it** and appends `CommitPushed`. Its `relevant` is yes when the last milestone in the artifact's history is "nothing yet" or a `Rejected`, so a rejection automatically hands the work back to the builder, with the gate's findings as `must_fix`.
5. **A gate judges** the unjudged commit and promotes it through the merge queue (`Promoted`), or rejects it (`Rejected` with findings: back to step 4), or escalates it to a person (`Escalated`: every team leaves it alone until a person resolves it with accept, retry or drop).
6. **Critics examine the merged work** and add findings, or a campaign creates fix tasks directly.
7. **The finding router turns findings into new artifacts**, which start again at step 1.

So the "delegation" is a set of preconditions written into each team's `relevant` method, plus the router and the campaigns, which create new artifacts. The work stops when no team finds anything relevant and no controller acts: `run_until_idle` then returns.

## 6. Inside one executor action

The executor holds most of the swarm's cleverness (`ExecutorTeam.act` and `_attempt`):

- **Which model first:** with routing on, the `Router` (`teams/routing.py`) picks the starting tier. Jev rates the task's difficulty once per artifact (`Profiled`), and the router looks up, per difficulty band and model, how often each model succeeded before (a SQLite ledger shared by all projects). It draws a plausible success rate per model (Thompson sampling) and starts on the model with the lowest expected cost per success among those whose draw meets the target (0.8 in the document runs). With no data, or when Jev's routing question is in shadow mode, it starts on the cheapest tier and only records what it would have chosen (`Routed` with `acted: false`).
- **Attempts and the cascade:** an attempt runs the model, puts back the acceptance tests, and runs the checks. A failed attempt before the last is thrown away (`AttemptFailed`) and the next attempt moves up to the next model that hasn't failed this step (`climb: true`). In the document runs the tiers are Haiku, then GPT-Sol; three attempts.
- **Repairs:** if the checks fail, the same model is first told exactly what failed and asked to repair (two repairs in the document runs), before the attempt counts as failed.
- **Nudge:** a model that claims to be done without changing anything is told so once, instead of losing an attempt.
- **Kept work:** an attempt whose checks pass but which says it isn't finished is committed and continued by the next attempt, not thrown away.
- **Disputes:** a builder may dispute an acceptance test; the referee rules, and an upheld test is skipped with the ruling as the reason.
- **Last resort:** when the last attempt fails, the artifact is escalated to a person; with `split_on_failure`, it goes back to the planner to be split instead (`Unbuildable`).

**Model calls themselves** (`llm/workers.py`): every call goes through a `Worker` that takes instructions, a typed Pydantic payload and an answer type, and returns an instance of that type. `LangChainWorker` uses LangChain's `create_agent` (tool calling plus structured output), with a cap on model calls per run. Every call's tokens and price are recorded as a `Spent` event. LangChain is imported only in `llm/` and `pipeline/graph.py`.

## 7. Judging

Two kinds of judgement exist, and they are kept apart:

- **Hard checks** (`gating/checks.py`): commands that pass or fail, such as running the tests or `tools/check_doc.py` for a document (word cap, citation format, headings). A failed hard check always rejects, without asking anyone.
- **Jev** (`judging/`, `jev/`): a hosted service that answers typed yes/no questions with calibrated probabilities. Judges are question sets (`judging/judges.py`); a threshold policy turns the probabilities into promote, reject or escalate (unsure). Each judge can run in *shadow* mode, where its answers are logged but not acted on, or *active* mode. Jev also answers the routing difficulty question, the decomposer's "does it fit?" and the supervisor's drift question.

**Note:** in the document runs Jev is in shadow mode (`jev: mode: shadow` in the generated `project.yaml`). So the gate there is the hard check alone, and Jev's answers change nothing: the router logs the difficulty Jev gave (`Routed` events with `acted: false`, every one of them in the run below) and starts every section on the cheapest model, Haiku. The outcomes still go to the routing ledger, so the router keeps learning for when it is switched on. Quality in a document run comes from the writer's structure, `check_doc`, the review campaign and the citation audit, not from a judge at the gate.

## 8. A real run, traced

The study from 2026-11-12 (`~/agentswarm-runs/ai-small-business-2`) shows what actually runs today. Its generated project has two levels (`draft`, `reviewed`), **one** team (`writer`, tiers Haiku then GPT-Sol; routing configured but not acting, so every section starts on Haiku), **one** gate (`draft -> reviewed`, hard check `check_doc`), and a brief. The review pass adds a `ReviewCampaign` with one perspective, `claims`.

Around the project sits the **document pipeline** (`pipeline/graph.py`, `pipeline/stages.py`): a LangGraph graph that orders the stages and pauses for the user, saving its state in SQLite between commands:

> find sources -> approve sources -> write -> (decide on failed sections) -> audit citations -> review -> audit -> approve publishing -> publish

1. **Find** (`evaluation/papers.py`): a model plans search threads and queries, Tavily searches the web, a model picks ten sources; the user approves them.
2. **Write:** the pipeline generates `project.yaml` and runs `Project.run_until_idle`. The **briefer** drafts the brief and an outline of 7 sections, fits their word shares to the cap, and creates 7 artifacts (`created_by: briefer`). The **writer** team picks each up, writes it in one typed answer, and the **gate** checks it with `check_doc` and merges it into `reviewed`. One section failed its checks on Haiku and climbed to GPT-Sol.
3. **Audit** (`evaluation/citations.py`): every cited statement is checked against its cited source by a model of another family (DeepSeek), in plain code outside the board.
4. **Review:** the same board is opened again with the review campaign added. The claims reviewer (Haiku) checks each cited statement in each section; the referee (DeepSeek) rules on what it flags. Over two rounds 14 attacks confirmed 30 problems, which became **12** new artifacts (`created_by: reviewer`, one per section and round with something confirmed). The writer revised each section, and the gate merged it again.
5. **Audit again, approve, publish** an HTML page.

The board recorded 395 `Spent` events, 19 artifacts, 19 commits and 19 promotions; the run cost $1.42.

## 9. Where the code departs from common practice, or from its own design

1. **A swarm in design, a pipeline in practice.** The board, claims, analyzers, decomposer, acceptance tests, the supervisor's drift check and Jev at the gates are all built and tested, but the document runs use one producing team, one gate with one hard check, Jev in shadow (so routing and judging only record), and a campaign driven by plain code. The loosely coupled teams of the design are, today, mostly the software domain's (the hardening runs and the conformance-suite projects). This matters for the lean-code goal: some of the catalogue may have no current user. That needs checking per part against the configs actually run, not settling here.
2. **Limited concurrency within a project** (section 3). A team now works on several artifacts at once, but different teams still take turns, and the review campaign attacks one part at a time, so a review round is sequential.
3. **Polling, not events.** Teams are woken by `run_until_idle` repeating whole steps, each re-scanning every artifact, rather than by subscriptions to new events. It is simple and crash-safe; it would cost time only with many artifacts.
4. **Two orchestrators.** The board is deliberately not a graph, yet the document run is a LangGraph graph around a board run. Both are fine for their jobs (a fixed workflow with pauses for a person; open-ended work on the board); a reader should just know that a document run has two layers of control, and that the review campaign's rounds, the writer's attempts and the pipeline's stages are three separate loops.
5. **The writer bypasses the agent.** For outline sections the `Writer` doesn't use the LangChain agent and its tools at all: plain code gathers the sources and asks for one typed answer. This is closer to common advice (prefer fixed workflows to agents where the steps are known), and it was adopted after tool-using writers re-read their sources until their call budget ran out.
6. **Two kinds of judges, by design.** Critics must bring evidence that plain code can check (a failing test, a verbatim quote), and a referee of another model family rules on the rest. This is stricter than the common LLM-as-judge setup, where one model's opinion is taken as is.
