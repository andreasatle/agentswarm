# One command from topic to document (goal 13)

Written 2026-11-01. The user's goal (COLLABORATION.md goal 13): the decisions about what runs belong in the framework, with a few approvals for the user, not in Claude's judgement during a chat. Today a document takes five commands, hand-picked flags and Claude stepping in: on the Venice guide, three times (sources added, a citation format fixed, a section accepted over its cap).

## What the user does

```
agentswarm write "how sleep affects memory" --reader "an educated adult, no biology" --budget 1.50
agentswarm runs                      # every run: its stage, its spend, what it waits for
agentswarm approve sleep-memory      # after reading what it shows
agentswarm publish sleep-memory      # a page for atle.dev; deploying stays the user's
```

A run lives in one folder (`~/agentswarm-runs/<name>/`) with `run.json`: the user's choices, the stage reached, the spend so far, and what it waits for. Nothing depends on a chat: the run can be stopped, resumed, or inspected at any time, and the next step is always printed.

## Stages

| Stage | What happens | Who decides |
| --- | --- | --- |
| 1. Start | Topic, reader, budget, optional source (openalex, arxiv, wikipedia), words, URLs, or a folder of the user's own files. Prints the plan with the expected cost of each stage | **the user** |
| 2. Sources | `find-papers`; downloads what it can | automatic |
| 3. Approve sources | Pauses. Shows the list, why each source, what must be fetched by hand (with links), the cost so far and the expected rest. The user edits the folder and approves | **the user** |
| 4. Write | Brief, outline and one writer per section, as `learn` does now | automatic |
| 5. Check | Citation audit; if fewer than 75% of statements are fully supported and the budget allows, one facts review and a new audit (at most two rounds) | automatic, by the policy |
| 6. Approve publishing | Pauses. Shows the document, the audit's numbers, the cost, and any part that failed | **the user** |
| 7. Publish | A static page written beside the run (`page.html`). The program never touches the homepage | automatic after the yes |

When something fails that today needs Claude (a section the checks keep rejecting, say), the run stops with the choices spelled out (`retry`, `accept`, `drop`) and what each means, instead of waiting for someone who knows the internals.

## The policy, not Claude

The decisions Claude makes by hand today move into one file of defaults (`policy.yaml` in the repository, overridable per run): the models per role, the words per reader, the review threshold (75%), review rounds (at most 2), the audit sample (20), the source cap, and what counts as "failed". Changing a decision is editing a number, and the run records which policy it used.

## The budget is a hard cap (audit A03, built in)

`--budget` is the whole run's limit, across every stage and across days. Every model call asks for admission first and reserves its worst case; a call that wouldn't fit is refused, and the run pauses with the reason. The machine-wide daily limit stays as a backstop. Costs are recorded as calls finish, so a crash loses none. This replaces today's check between attempts, which let a trial estimated at $0.30 cost $2.16.

## How it is built

A plain-code state machine over the stages, its state in `run.json`, calling the existing pieces (`PaperFinder`, `DocumentImport`, the project runner, `CitationAudit`, the page renderer now in a scratch script). No new team: the board keeps doing the writing and reviewing inside stage 4 and 5. Pause and approve reuse the board's own pause and resume.

The alternative is LangGraph, whose checkpointed graphs with interrupts are made for exactly this (a fixed workflow that pauses for a person). It is the user's learning goal and would fit here; the cost is a second persistence mechanism beside the board. Recommendation: the plain state machine first (seven stages, one JSON file), LangGraph if the workflow grows branches.

## Not in this step

Open-web research (goal 14), software projects through the same command (they have their own definition of done; later), and making the picker cover every thread of a request (a known gap from Venice, small, can come with stage 2).

## Questions for the user

1. Are the three approvals right (start, sources, publishing), or one more after the outline (today `--check-brief`)?
2. Should `publish` write into the homepage repository directly, or only produce the page file?
3. Plain state machine first, or LangGraph from the start (the learning goal)?

## Decisions made during implementation (2026-11-01)

- **LangGraph, thin** (the user's choice after the trade-offs: a fixed workflow that pauses for a person is what it is for, and learning it is a goal). The stages are plain classes in `pipeline/stages.py`, each behind a Protocol, so the graph is tested with fakes; `pipeline/graph.py` only orders them, pauses with `interrupt`, and keeps every run in a SQLite checkpointer (`~/agentswarm-runs/runs.sqlite`, one thread per run). AGENTS.md allows LangGraph there, the one exception to keeping it in `llm/`.
- **Commands:** `agentswarm write TOPIC --reader ... --budget ...`, `agentswarm runs [NAME]`, `agentswarm approve NAME [--choice ...] [--budget ...]`. Each prints where the run is and the exact command that answers it.
- **Approvals:** the sources and the publishing (the user's first two questions were not answered yet; three pauses are the default, a fourth after the outline can be added). Failed sections and a stage the budget won't fit pause too, with the choices spelled out.
- **Publish writes the page beside the run, and nothing else** (the user, 2026-11-01: "I don't want to update the homepage everytime. Just when I tell it to"; 2026-11-02: "I don't want the coupling to the homepage", "I'm not building a microsoft product where everything is mixed together in a big mess"). Approving writes `page.html` beside the run. The homepage is a separate project: putting a page there is a job done outside AgentSwarm when the user asks for it. The short-lived `agentswarm publish NAME`, the studio's homepage button and `policy.homepage` were removed on 2026-11-02.
- **The budget, honestly:** before each paid stage the run's spend so far (every stage, every day, from the machine's ledger under `run-NAME` and `doc-NAME`) plus the stage's expected cost must fit the budget, and the document project's own cap is set to what the run has left. That stops the run between stages and within the writing and reviewing; it is not yet the audit's A03 (admission before every single model call, reserving a worst case), so a stage can still overshoot by its last calls. A03 itself stays the next step for unattended runs.
- **Settings in one file:** `pipeline/policy.yaml` (papers, words, review threshold 75%, one review round, audit of 20, the cost estimates, the brief's template). The estimates come from the runs of 2026-10-18/19.
- **Each stage runs in a fresh thread** (the first real run, 2026-11-01): LangGraph hands its configuration to whatever runs inside a node through context variables, so a model call, a LangChain agent and itself a LangGraph graph, took the pipeline's synchronous checkpointer as its own and failed ("SqliteSaver does not support async methods"). A new thread starts with no context; a test runs a graph inside a stage. A run stopped by an error is kept: `runs` says where and why, and `approve NAME --choice retry` restarts it from that step. Our saved types are named to the checkpointer's serializer, which otherwise warns and will refuse them.

## What the user needed on the first real run (2026-11-01), the list goal 13 grows from

1. The commands written with `uv run` (fixed in the printed hints).
2. A model call inside a stage failed on LangGraph's checkpointer (fixed: stages in a fresh thread), and a crashed run had no way on (fixed: reported, `--choice retry`).
3. The expected cost ($2.27) was over the budget ($1.50) without saying what that meant (fixed: the plan step by step, and that the run will pause before a step that won't fit).
4. None of the five sources downloaded (publishers refuse programs): the user fetches them by hand. Approving an empty folder would have crashed the writing; now refused. The real fix is goal 14 (sources a program may fetch) or the picker preferring reliably free copies.
5. The writing stopped at once: the stage was built with no teams ("unknown teams ['writer']"), which the tests' fakes hid (fixed: the standard teams, and a test that checks the writing's and the review's configurations against the teams the stage really has). Nothing was spent.
6. The writing worked (8 sections, 5,939 words) but the run assembled and audited an empty folder: the sections live on the board repository's branches and were never copied out, so it reported 0 sections, audited 0 of 0 statements, read that as 0% and paid $1.35 for a facts review (which did improve the real sections). Fixed: the level is copied out before assembling and auditing; a writing or review that leaves no document, and an audit that finds no cited statement, stop the run with an error instead of going on. The waiting run was repaired by hand once (its state given the real document).
