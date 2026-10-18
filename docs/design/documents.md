# Documents with reviewers: design (built 2026-10-16)

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

## Decisions made during implementation

- **One campaign machine for red teams and reviewers** (`teams/campaign.py`): rounds over part × focus, done after quiet rounds, a score when a round is whole. The Hardener and the `ReviewCampaign` supply the parts (spec sections; the document's section files), the focuses (`AttackFocus`; `Perspective`) and the attack. A campaign counts only attacks on its own focuses, so changing focuses starts the rounds over instead of mixing old and new.
- **A finding's fix has no acceptance check of its own.** A test can be made to pass; a finding can only be judged. Rather than a model call inside every check run, the next round settles it: the same perspective meets the revised section again, and an unresolved finding comes back (the referee's earlier ruling stands beside it). The writer's checks stay plain code: `check_doc` (headings, one sentence per line, citations naming a source, the length cap).
- **Plain code before the referee:** each quote must be in the file it names, word for word, at least 12 characters, and in the document, its sources or the brief; the evidence must fit the perspective (`ReviewCampaign.NEEDS`: a coverage finding quotes a source, a consistency finding two document passages). The referee sees each quote with 600 characters around it.
- **Scoring is a `Measure`, now async,** so a score may call models: `QuestionProgress` reads the guide on the base level, drops the questions the reader answers from memory once (kept beside the questions as `memorized.json`), and scores the rest; `Scored.seen` is empty for a guide (the agents see no questions).
- **The baseline is a perspective:** `general`, one broad reviewer, the same machinery (`examples/guide/baseline.yaml`), so the comparison is at equal cost on the same built guide.
- **Models:** the writer by the router (Haiku 5.5, Sol), the outliner Sonnet, the reviewers Sol, the referee and the grader DeepSeek, the reader Haiku 5.5.

## First experiment (2026-10-16, $3.40): no gain on the hidden questions in either arm

**Setup:** six summarization papers (125,000 characters of sources), a guide capped at 3,000 words, 27 hidden questions of which 5 were dropped as answerable from memory, 22 kept. Built once by Haiku 5.5 for $0.05: 7 sections, 1,403 words, 8 of 22 questions answered. Then two arms on copies of it, $2 each: four perspectives, and one broad reviewer.

| Arm | Cost | Reviews | Confirmed / reported | Revisions merged | Words | Hidden questions |
| --- | --- | --- | --- | --- | --- | --- |
| Four perspectives | $1.65 | 46 (2 rounds) | 43 / 46 | 41 | 3,074 | 8 → 8 (dipped to 7) |
| One broad reviewer | $1.68 | 54 (8 rounds) | 41 / 42 | 40 | 2,889 | 8 → 6 (peaked at 10) |

Per perspective: coverage 20 of 21 confirmed, reader 19 of 20, facts 4 of 4, consistency 0 of 1. Scoring the same text three times gave 8/22 each time, so the scorer is stable; the movement came from the edits.

**What it says:**
- **The reviewers changed the guide a lot, but not toward what the questions ask.** Both arms doubled the guide to its length cap. The questions are practitioners' details (which baselines, which ROUGE variants, the dataset's size and source, how many annotators), while the brief asks for a guide a newcomer can understand; the coverage and reader reviewers filled the cap with method explanations, which serve the brief, and few of those details. The measure doesn't match the goal it is meant to measure: the exam has to ask what the brief promises.
- **The referee confirmed almost everything** (84 of 88 findings). On software, a failing test and the spec's words gave it something to reject; a finding about a document is a judgment, and the referee agreed with nearly all of them. As a filter it did little here.
- **Facts held:** the facts reviewer found 4 claims to fix in 12 reviews; the guide stayed cited throughout (`check_doc`).

## Second try (2026-10-16, $1.27): a reference guide and a stricter referee; still no gain

**Changes:** the brief asks for a reference guide (each paper's aim, method, data, baselines, metrics and results, with numbers), matching what the hidden questions ask; the referee sees the whole document and answers *shown*, *matters* and *fits* separately, a finding confirmed only if all three hold. The build took a retry (Haiku ran out of steps before the relations section): $0.32, 1,347 words, **10 of 22** questions answered (the newcomer guide answered 8).

| Arm ($0.50 each) | Reviews | Confirmed / reported | Merged | Words | Hidden questions |
| --- | --- | --- | --- | --- | --- |
| Four perspectives | 15 (round 1 not finished) | 13 / 16 | 11 | 1,923 | 10 → 9 |
| One broad reviewer | 11 (2 rounds) | 18 / 20 | 15 | 2,001 | 10 → 11 → 9 |

**What it says:** the reference brief helped the first draft (10 vs 8), but reviewing still moved nothing beyond a question either way. The stricter referee confirmed a little less (31 of 36 vs 84 of 88). With 22 questions, one question is 4.5 points, and at $0.50 an arm the four perspectives didn't finish one round: the experiment can't separate the arms. Two things are missing to learn more: **per-question records** (which questions a revision gained or lost, to see whether revisions displace facts under the cap or add facts no question asks about) and **more questions** for the same money.

## The measure was the noise (2026-10-16/17, about $0.45)

**Retrace:** every merged version of the four-perspective arm (12 versions) re-scored question by question. The first version scored 8 here and 10 when the arm ran: same text. Of the 22 questions, 4 were always answered and 6 never; the other 12 flipped 2 to 7 times across the versions, including every question about a paper whose section no revision touched. One reading per question at the models' default temperature varied by about two questions on the same text, as large as any revision's effect: neither arm's result could be read.

**Fixed:** the reader and grader have their own model entries at temperature 0, and each question is decided by the majority of three readings, stopping once two agree (`questions.votes`). Three scores of the same guide then gave 10, 9, 10: the noise is down to about one question, not gone (the grader reasons before it answers, and temperature 0 doesn't make that fully repeatable). A comparison now needs an effect of more than two questions, or more questions; the next run should use more papers, so one question weighs less than 4.5 points.

## Ten papers (2026-10-17, about $5.30 with the false starts)

**Setup:** ten summarization papers (220,000 characters), 44 questions of which 8 were dropped as answerable from memory (36 kept), a 4,000-word cap. One task for the whole guide ran out of steps twice ($0.31: first a clash between the executor's guard against repeated reads and the context trimming that tells the model to read again, fixed; then simply too much reading for one task), so the guide was built as one task per paper plus an introduction: 11 tasks, all merged, $0.22, 2,928 words.

**A false start:** the OpenAI account ran out of credits, every review by Sol was refused, and the refusals counted as quiet attacks until the campaign called itself done. Fixed: a failed call is no attack, three in a row stop the run with a notice; Sol via OpenRouter.

**The arms, $1.75 each:** four perspectives made 35 reviews (round 1 not finished), 30 reported, 26 confirmed, 25 merged; one broad reviewer made 19 reviews (2 rounds), 30 reported, 29 confirmed, 27 merged. Both filled the 4,000-word cap.

**One more flaw in the measure:** questions flipped with no change to the text, because the reader wasn't told which of the ten papers a question was about ("which is the baseline?" got "it depends on the paper"). Fixed: each question names its paper. Scored again, twice each:

| Guide | Score 1 | Score 2 |
| --- | --- | --- |
| Built, before reviews | 18 / 36 | 15 / 36 |
| After four perspectives | 19 / 36 | 19 / 36 |
| After one broad reviewer | 17 / 36 | 17 / 36 |

**What it says:** the four perspectives come out about two to three questions above the starting guide and the broad reviewer about one, but the same starting guide scored 18 and 15: the difference is suggestive, not shown. Naming the paper raised every score by about seven questions: most of the earlier "low" scores were the measure's fault. To decide the question, a run would score each version several times (the cost of scoring is now a large share of a run) and give the perspectives the budget to finish two rounds.

## Real use: learn, and a citation audit (2026-10-18)

**The path:** `agentswarm learn` (papers, what the reader knows, what they want) imports the PDFs as parts, drafts a brief and outline, writes each section in one call from its assigned parts (with 300 words of each neighbouring part), and assembles one Markdown file with (Author et al., year, p. N) citations and a bibliography from the title pages. Fixes on the way, each from a real failure: writers that read with tools re-read until their calls ran out (now one call per section); trimming; sections far over budget (rewritten once); page and grouped citations mended in plain code; sentences, lists, abbreviations and LaTeX handled by the splitter and the checker; a PDF's title page in layout mode, which keeps the author line.

**The JEPA tutorial** (five papers, 97,000 words; the user knows the field): about $0.20 a version without review. An outside review (ChatGPT) rated the first version a strong literature review but a weaker tutorial; a teaching brief (concepts first, papers as evidence, equations motivated, a side-by-side comparison) changed the structure as asked.

**Citation audit** (`agentswarm audit-citations`: 20 cited statements drawn at random, each checked by the referee against the cited part and page):

| Version | Supported | Partly | Not supported | Named page right |
| --- | --- | --- | --- | --- |
| Research version | 14 | 4 | 2 | 8 of 9 |
| Teaching version | 13 | 4 | 3 | 9 of 11 |
| Teaching version after one facts review ($1.11, 7 of 9 sections) | 17 (seed 1), 14 (seed 2) | 2, 4 | 1, 2 | 11 of 13, 9 of 12 |

The facts review lifted the same sample from 13 to 17; a fresh sample gave 14, so over 40 statements about 78% are fully supported against 65% before. The typical miss is an overstatement ("match" for "outperform", "the authors stress") or the wrong page, rarely an invented fact.

**After the whole facts round** (reviews in one call since, about $0.10 each; 9 sections reviewed, 7 fixes merged): three samples of 20 gave 16, 14 and 15 fully supported, so about 75% fully supported over 60 statements, 18% partly (overstated or narrower), 7% not supported; named pages right 34 of 38. One facts round lifts the share from about 65% to about 75%; the rest are mostly overstatements, which a reviewer looking at a whole section misses and a check of each statement against its source catches.

## Finding the papers (2026-10-18)

The user: "ask for a topic and you come up with documents", then `learn` as before. `agentswarm find-papers TOPIC --know ...` has the planner model write five keyword queries and name the well-known papers it is sure of; plain code runs the queries on arXiv's API and looks each title up there; the model picks five from the pooled candidates (titles, authors, abstracts) with a line each on what it adds; plain code downloads the PDFs into a folder with `papers.md`. The user edits the folder, then `learn FOLDER`. Decisions: arXiv, because its papers are open and real (a model naming papers from memory invents some), so no reference can be made up; titles named by the model are kept only when arXiv has one with the same words; known titles are asked for because arXiv's relevance ranking puts recent small papers first (a search for process reward models missed "Let's Verify Step by Step"); a quoted phrase search, because a word-by-word title query found nothing; no citation counts (Semantic Scholar) until a list shows the need; the approval is the folder itself, no pause machinery. Paywalled journals and old classics are not on arXiv: the user adds those by hand.

**First use** (test-time compute, the same goal as the user's own five papers): the pick cost $0.15 (about 80 whole abstracts; since capped at 40 candidates of 600 characters) and chose Cobbe et al. 2021, Wang et al. 2022, Lightman et al. 2023, Snell et al. 2024 and DeepSeek-R1; two of them in the user's set. The tutorial ($0.22, 11 sections, about 7,000 words) gained a section on compute allocation (Snell et al.) that the user's set lacked. The briefer dated DeepSeek-R1 2026 from its second version's PDF; `find-papers` now keeps each paper's arXiv record (papers.json), the import turns it into the seed's references, and the briefer's references give way to those given.
