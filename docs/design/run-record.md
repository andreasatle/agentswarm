# The run record (priority 2, 2026-11-11)

Priority 2 of the list agreed with the user on 2026-11-11: tie every outcome on a board to the model, the prompt version and the check that judged it. Learning from our own runs (priority 4, prompt improvement from failed steps), the error correlation between models (priority 3) and the competitiveness study (stage 4) all need this dataset; until now part of it was missing or only implied.

## What was missing

Found by reading the board of the LLM-scaling run (879 events):

- **No prompt version anywhere.** A prompt changed in git between runs left no trace on the boards, so an outcome could not be tied to the wording that produced it.
- **The model by its project name only.** `Spent` recorded "planner" or "haiku55"; the model itself was only in `Routed` (routed roles) and the project config, which can change between runs.
- **Verdicts lost.** The claim checker's verdicts survived only as flags the referee ruled on: the statements it passed were not recorded, which is what calibration and error correlation need (the checker calibration had to rebuild them by rerunning the checks). The citation audit kept only its tallies.

## What was built

- **`PromptId`** (core/domain.py): the answer's type and the first 12 hex digits of a SHA-256 over the system prompt, the answer's JSON schema, and each tool's name, signature and description: everything the model is given except the task data. `Prompts.identify` computes it; a `LangChainWorker` reports it with each call's usage. The prompt catalogue (docs/PROMPTS.md) prints each caller's version, so a version in a record leads to its wording, and git history to older ones.
- **`Spent` carries `model_id` and `prompt`**, so each call on a board says which model, with which prompt.
- **`Checked`**: one event per cited statement a model check judged, the passes too: the claim checker's in a claims review (each line once, when it was judged; a cached verdict isn't recorded again) and the citation audit's in a document run.
- **`RunRecord`** (monitor/record.py) and `agentswarm record <root>...`: one JSON line per verdict, read only from the board: what judged (claim checker, audit, referee ruling, a routed call's outcome), the verdict, the statement or artifact, the version judged, and the makers on both sides: the models and prompts of the calls that wrote the section (its writing task and the revisions made of it, up to the verdict) and of the check.

## Decisions made during implementation

- **A derived view, not a new log.** The board already holds every event; the record is a join over it (the spend of a role, the artifacts that wrote a section), so it can't drift from what ran, and older boards give their rows too. Standard practice for traces (OpenTelemetry's GenAI spans, LangSmith runs) records the model and a prompt version on each call and joins outcomes to calls afterwards; this is the same shape on our event log.
- **The version is a content hash, not a hand-kept number.** A number someone must remember to bump drifts (the lesson of conventions held by memory, goal 9); a hash changes exactly when the wording, the schema or the tools do. The name (the answer type) keeps it readable.
- **Makers are joined by role, not by call id.** A check's model and prompt are read from the spend recorded under its role ("claim-checker", "citation-audit", "referee"); within a run a role has one model and one prompt, and when it has more (a prompt changed between runs on the same board, a revision by a stronger model) the row lists them all rather than guessing. Per-call ids would be exact but touch every worker call site for no measured need yet.
- **Older boards.** They have no prompt versions (`prompt` is null). Their model names are resolved by the board's `Routed` events, then by the run's config (`project/project.yaml` beside a document run's board, or `--config`): on the LLM-scaling board every row found its makers, and the referee resolved to DeepSeek V4 Pro.
- **Not covered yet:** the source finder runs before a run has a board (its spend goes only to the machine's ledger), and software runs' gate verdicts appear as routed calls' outcomes, not per check. Neither is needed by priorities 3 to 5; they get added when a measurement needs them.

## First use

`agentswarm record ~/agentswarm-runs/llm-scaling-2/board` gives 85 rows: 49 referee rulings (23 confirmed, 26 rejected) and 36 outcomes of routed calls, each with its writer's and checker's models. Claim-checker and audit verdicts start with the next document run.
