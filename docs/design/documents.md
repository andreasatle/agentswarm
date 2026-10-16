# Documents with reviewers: design (draft, 2026-10-16)

## The question

The user's goals 1 and 12: the framework should take any goal and harden the product toward it; software is only the first sample. Documents have no tests, so their critics are mainly **perspective reviewers** (the user, 2026-10-16: "very important for the document writer"), with a **red team** where a failure can be demonstrated (a false claim against its source). The question this design answers: **do perspective reviewers make a document measurably better, which perspectives pay for themselves, and what does it cost?** It is also the first real test of whether the framework is general.

## What the evidence says

| Finding | Source | Consequence for the design |
| --- | --- | --- |
| Writers that research a topic from several discovered perspectives produce articles that experienced Wikipedia editors judge better organized (+25% absolute) and broader in coverage (+10%) than an outline-driven baseline | [STORM, NAACL 2024](https://aclanthology.org/2024.naacl-long.347) | Perspectives are worth having; each reviewer has one perspective, and coverage is one of them. |
| A long text's factual precision can be measured by splitting it into atomic facts and checking each against a knowledge source; an automated estimator stays close to human judgment | [FActScore, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.741) | Accuracy is measured against the project's own sources, claim by claim; the red team's evidence is a claim and the source passage it contradicts or lacks. |
| Questions answered from a text alone measure what the text conveys (used for summaries' faithfulness and coverage) | [QAGS, 2020](https://arxiv.org/abs/2004.04228); [QuestEval, 2021](https://arxiv.org/abs/2103.12693) | The hidden tests of a document are **hidden questions**: a separate reader model answers them from the document alone; graded against known answers. |
| A dataset of questions written by practitioners who saw only a paper's title and abstract, answered with evidence by others from the full paper: 5,049 questions over 1,585 NLP papers, CC BY 4.0 | [Qasper, NAACL 2021](https://aclanthology.org/2021.naacl-main.365) | A ready answer key written by people, independent of us and of the swarm: what a reader wants to know about a paper. |
| Model judges prefer their own family's output, the first option shown and longer answers | [Zheng et al., 2023](https://arxiv.org/abs/2306.05685); [Panickssery et al., 2024](https://arxiv.org/abs/2404.13076) | A reviewer's finding is a claim, not a fact: it must quote the document, a referee of another family rules, and the writer may dispute; length is capped by the brief, so reviewers can't win by asking for more text. |

## The problem

A **newcomer's guide to a small set of research papers**, written only from those papers (the sources), measured by the papers' Qasper questions, never shown to any agent.

- **Sources:** 4 to 6 related NLP papers from Qasper's test split, their full text in `sources/`. Closed sources make accuracy checkable: every claim must cite a source passage.
- **The brief:** "a guide that lets a newcomer understand what these papers do, how, and what they found", with a length cap (e.g. 3,000 words).
- **Hidden measure 1, coverage:** each paper's Qasper questions (about 3 per paper; 15 to 20 in all), answered by a reader model from the guide alone, graded against the annotators' answers. Questions the reader answers correctly with no guide at all (from its own memory of the paper) are dropped first, so the score measures the guide, not the model.
- **Hidden measure 2, accuracy:** the share of the guide's claims supported by the passage they cite (FActScore-style, on all claims or a fixed sample).
- **Baseline at equal cost (RESEARCH.md P8):** the same writer with no reviewers, the money spent on rewrites instead. Reviewers must beat it.

Alternatives considered: a STORM-style article on an open topic (needs web search and an answer key we'd have to make); a guide to jq or YAML from their specs (no independent questions exist). Qasper is the only one with a human answer key.

## The teams

The documents domain exists (Phase 5): an outliner, a writer, gates draft → reviewed → final → published, and reviewers whose every finding must quote the document verbatim. They review one change at a time. What changes is the same as for software: critics attack the **whole product**, round after round, and every confirmed finding becomes a fix task.

| Critic | Kind | Evidence (checked by plain code first) | Then |
| --- | --- | --- | --- |
| Fact checker | red team: demonstrates a false or unsupported claim | the claim, quoted from the guide, and the source passage it cites, both verbatim; or a claim citing nothing | referee: does the passage support the claim? |
| Coverage | reviewer | a source passage, verbatim, about something the brief asks for that the guide doesn't cover | referee: does the brief ask for it, and is it really absent? |
| Reader (a newcomer) | reviewer | a guide passage, verbatim, and what a newcomer couldn't follow | referee |
| Consistency | reviewer | two guide passages, verbatim, that disagree | referee |

The campaign is the Hardener's: section × critic, rounds until two whole rounds confirm nothing, or the budget ends. What a fix task's acceptance check is differs from software, because a document has no test to make pass: **the finding itself**. When the writer is done, the referee is asked of the revised section "is this finding resolved, without making the guide break the brief?"; plain code checks the citations and the length cap. The writer may dispute a finding, quoting the brief or a source, as builders dispute checks.

## How a perspective is measured

The same as a red team focus: per perspective, its reports, confirmed findings, fixes merged, and the change in both hidden measures after each round, with dollars per point gained. No perspective is switched off for being quiet (the user, 2026-10-15). A perspective that confirms findings but never moves either measure is the interesting case: either it improves something the measures miss (clarity), or its findings are noise; the per-round record shows which, and the reader perspective may need its own measure (e.g. the hidden questions answered by a deliberately weak reader model).

## What gets built

| Part | Size | Exists? |
| --- | --- | --- |
| The Qasper importer: papers as sources, questions as the hidden set, the memory filter | medium | the SeedImport pattern |
| Hidden-question scoring: reader and grader models, run after each round | medium | scoring exists (`Scored`) for tests |
| Citations in the guide and their plain-code check (`[S2]` → a passage in `sources/`) | small | `tools/check_doc.py` |
| The campaign over sections × critics with quote evidence | medium | the Hardener, with an evidence strategy instead of a failing test |
| A finding as the acceptance check of its fix task, ruled by the referee | small | the referee, disputes |
| Factual precision against the sources | small | the referee's passage checks |

About two to three days of work, no paid model calls (tests use fakes). Then: the dataset download (Qasper, about 10 MB, after a yes), and one paid run with a one-line plan and a cap of about **$5**: the baseline first, then reviewers on the same sources.

## Risks and guards

- **The reader model knows the papers from training:** questions answered correctly without the guide are dropped before the run; papers from after the models' training would be better still, but have no answer key.
- **Reviewers that ask for more and more text** (judges prefer length): the brief's length cap is a plain-code check; a finding that can only be fixed by exceeding it is not confirmed.
- **Findings of taste, not substance:** every finding needs verbatim evidence and the referee's ruling; the per-perspective record shows which ones move nothing.
- **Hidden questions too specific for a newcomer's guide:** the baseline measures what a plain guide reaches; the question is the gain over it, not the absolute score.
