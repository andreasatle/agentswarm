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
| 7. Publish | A static page written into the homepage repository, listed under Writing. Deploying stays the user's | automatic after the yes |

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
- **Publish** writes the page into the homepage repository (`policy.homepage`) and lists it in its `src/web/tutorials.json`, which the site now reads; committing and deploying stay the user's. Without a homepage, the page lands beside the run.
- **The budget, honestly:** before each paid stage the run's spend so far (every stage, every day, from the machine's ledger under `run-NAME` and `doc-NAME`) plus the stage's expected cost must fit the budget, and the document project's own cap is set to what the run has left. That stops the run between stages and within the writing and reviewing; it is not yet the audit's A03 (admission before every single model call, reserving a worst case), so a stage can still overshoot by its last calls. A03 itself stays the next step for unattended runs.
- **Settings in one file:** `pipeline/policy.yaml` (papers, words, review threshold 75%, one review round, audit of 20, the homepage, the cost estimates, the brief's template). The estimates come from the runs of 2026-10-18/19.

