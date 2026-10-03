# Phase 5: A second domain, documents

Status: **planned** (decided on the recommendations, as for Phases 3 and 4). Proposal sections: §3 G2
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
