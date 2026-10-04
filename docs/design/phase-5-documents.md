# Phase 5: A second domain, documents

Status: **complete** (decided on the recommendations, as for Phases 3 and 4). Proposal sections: §3 G2
(domains are configuration), §9 (documents in git), §10.3 (document teams), §17.

**Exit criterion (§19):** the same engine produces a multi-section document through all levels.

## What has to change, and what must not

G2 says software and documents are configurations, not separate code. The engine (board, claims, gates,
trains, joins, router, supervisor, judges, decision log) stays exactly as it is. What is software-specific
today, and becomes a choice:

| Today | Problem for documents | Change |
| --- | --- | --- |
| Analyzers need a failing **test** as evidence | Prose has no tests | Evidence is a strategy: tests for code, **quotes** for text (a reviewer must cite text that really is in the document; the code checks the quote verbatim) |
| Team instructions are written for code | A writer is not a programmer | Document teams are the same classes with document instructions: `writer` (an executor), `outliner` (a decomposer) |
| The only gate judge asks about code changes | "tests relevant" means nothing for a section | A document judge, `SectionReadiness`: in scope, clear, complete |
| Hard checks run pytest | | Checks are commands already: a document project ships its checker in its seed (`tools/check_doc.py`), run in the sandbox like any check |

## Design

### Document layout (§9)

One file per section under `sections/`, one sentence per line, so line-based git merges and diffs work
on prose. The seed's `tools/check_doc.py` checks the rules deterministically: every section starts with a
heading, has text, keeps one sentence per line, and every relative link points at an existing file.

### Teams

| Team | Watches | Is | Evidence |
| --- | --- | --- | --- |
| `outliner` | draft | the decomposer, with document instructions | (splits, like the decomposer) |
| `writer` | draft | the executor, with document instructions | the checker passes |
| `consistency_checker` | reviewed | an analyzer: terms and claims agree across sections | quotes |
| `fact_checker` | reviewed | an analyzer: claims that may be wrong or unsupported | quotes |
| `audience_reviewer` | final | an analyzer: fit for the intended reader | quotes |

Each analyzer has a relevance judge (does this text make claims worth checking; does it use terms
other sections also define; ...), logged and scored automatically like the software ones.

### Judges

`SectionReadiness` at `draft -> reviewed`: "Does the section stay on its topic?", "Is it clear to its
intended reader?", "Does it cover everything its task asks for?", each with `Fails(...)`. Above that,
release trains with required sign-offs, as for software.

### Preset and example

`presets/document.yaml`: levels `draft, reviewed, final, published`; the `check_doc` check; the five
teams; gates `draft -> reviewed` (judged), `reviewed -> final` (consistency and fact sign-offs),
`final -> published` (audience sign-off, a person approves). `examples/handbook/`: a small handbook
project with its seed and checker.

## Steps

1. Evidence strategies for analyzers; quote evidence and a `DocumentReviewer`.
2. Document teams in the catalog: `writer`, `outliner`, the three reviewers; their instructions.
3. `SectionReadiness` and the document relevance judges.
4. `presets/document.yaml`, `examples/handbook/` with `tools/check_doc.py` (and its own tests).
5. End to end with fakes: a goal outlined into sections reaches `published`.
6. A live run.

## Decisions made during implementation

**Step 1 (evidence strategies).** `Analyzer` keeps relevance, sign-off, escalation and the relevance
outcome; subclasses implement `examine` and decide the evidence. `ProbingAnalyzer` (code): scratch tests,
a problem needs a failing one. `ReviewingAnalyzer` (text): a read-only toolbox, and a problem needs a
quote of at least 12 characters found verbatim (whitespace runs normalised) in the file it names; invented,
too short or misattributed quotes are dropped. Named `ProbingAnalyzer`, not `TestingAnalyzer`, because
pytest collects any imported class whose name starts with `Test`.

**Steps 2–5 (teams, judges, preset, end to end).**
- `teams/documents.py`: `Writer` (an `ExecutorTeam` with writing instructions: Markdown, one sentence per
  line, a section per file), `Outliner` (a `Decomposer` that splits a document into sections),
  `ConsistencyChecker`, `FactChecker`, `AudienceReviewer` (`ReviewingAnalyzer`s). New finding kinds
  `unsupported` and `unclear`. Executors wait for sizing when the project has any planner (decomposer or
  outliner), and either planner brings the join gate.
- Judges: `SectionReadiness` (on topic, clear, complete, each with `Fails`) and one relevance judge per
  reviewer.
- `presets/document.yaml`: draft → reviewed → final → published; `check_doc` (from the seed); the five
  teams on `models.main`; a judged first gate, sign-off trains to `final` and `published`, a human
  publishing gate. `examples/handbook/project.yaml` needs only a north star, a model and its Jev modes
  (`size:*`, `split:*`, `join:*` active, so the outliner really splits).
- **Exit criterion in tests** (`tests/domains/test_documents.py`): a goal outlined into two sections is
  written, judged, reviewed by all three reviewers and, once a person approves, published, the umbrella
  with it. No engine class changed for the domain beyond step 1's evidence strategies.
- `ScriptedWorker` (tests) answers by output type, so one fake model can serve every team in a run.

**Step 6 (live run, 2026-10-04).** `examples/handbook/project.yaml`, gemma4:e4b writing and reviewing,
Jev active for size/split/join, Docker sandbox, run in `~/agentswarm-runs/handbook-1`.
- **The size check judged the book-club task to fit one branch**, so the outliner didn't split it.
  Splitting and joining are covered by the offline end-to-end test; a live split needs a larger goal.
- **The path:**
  - the writer wrote four sections (the seeded introduction plus choosing books, running a meeting,
    keeping members) and `check_doc` passed;
  - the gate promoted draft → reviewed;
  - the consistency and fact checkers signed off, and the train promoted reviewed → final;
  - the audience reviewer signed off, and the human gate escalated;
  - after `agentswarm resolve … --accept`, the next run promoted final → published.
- **The reviewers left two minor findings,** both specific and quoted from the text: a UK/US spelling
  mismatch between the north star and the introduction, and "group dynamics" as jargon. Minor
  findings don't block or create fix tasks, by design, so they stay visible on the board for a person.
- **12 model runs, unpriced** (a local model): $0.00.
- **Phase 5's exit criterion holds live:** the second domain reuses the same board, gates, trains and
  human approval with only a preset and its checks.
