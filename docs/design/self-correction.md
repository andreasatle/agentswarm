# Self-correction: a held-out benchmark, typed sentences and a reader critic

Started 2026-11-13, after the AI-workflows posts. The user asked whether the stream of small fixes makes a better swarm ("are we overfitting, creating a fix for every problem that doesn't generalize?") and whether the agents shouldn't find and correct errors themselves rather than Claude patching each one. Agreed plan (the user, 2026-11-13): a reader critic, structural rules only where they remove a whole kind of error, and Claude's role narrowed to bugs in the machinery (COLLABORATION.md rule 8); each measured on a held-out benchmark.

## The problem

Every fix of the last days was found on the run that showed the flaw and checked by a unit test, never by whether documents got better overall. Some removed a cause and generalize (word-cap rewrites, timeouts escalated, failed checks recorded, parallel teams). Others patch one symptom of free-text output: "v." and "J." treated as abbreviations, citations moved around by regular expressions, bracket tags trimmed. The writer returns free text and plain code guesses where sentences end; each patch improves the guess, none removes it. And form and clarity have no critic at all: the review step checks claims only, so Claude was the critic.

## The benchmark (evaluation/benchmark.py, benchmark.yaml)

Three held-out cases on sources already on disk, never used to find fixes: the LLM-scaling analysis (16 web pages and papers), the sleep tutorial (3 papers), the test-time-compute tutorial (5 papers), each at 2,000 words. `agentswarm benchmark <label>` writes each with the current code, answers its pauses (yes to sources and publishing, a failed section accepted so it counts), and appends one record per case to `~/agentswarm-data/benchmark.jsonl`:

- cost, minutes on the board, spend per model (the strong model's share shows escalations);
- the last citation audit's share of fully supported statements;
- reader-facing flaws found by a held-out judge (`ReaderJudge`: Sonnet 5.5, not part of any run), each with a verbatim quote that plain code checks, in four kinds: broken text, citation form, unclear, repetition.

A round costs about $2.50-4. Variance between rounds is unknown until a round is repeated; differences smaller than it are not results.

## The steps, each measured against the benchmark

Revised 2026-11-13 after a review by Codex, which the user brought in: it counted five prose heuristics in the document checker (several sentences on a line, a lowercase start, a start with punctuation or a citation, unbalanced brackets) and six repairs that guess what the writer meant (citations split, mapped to parts and moved, prose split into sentence lines). Moving a citation from the start of a line onto the previous sentence decides which sentence it supports: a semantic guess, to be removed. It also found that the review runs only when the citation audit falls below 90%, so a document with well-supported citations is never reviewed for anything else.

1. **Round 0, baseline:** the code as of 2026-11-13.
2. **Structured output:** the writer returns its section as typed parts (paragraphs as lists of sentences, each with the sources it cites; lists; equations), and plain code lays them out as Markdown. Its consumers change with it: the claim checker reads sentences and their citations from the structure instead of from line breaks, and the document check keeps only its structural rules (Markdown files, headings, text under them, citations naming real sources, a citation per section, the word cap). The sentence splitter, the abbreviation lists and the citation repairs are deleted with the prose rules, in the same change: until then they are the only guard against broken text. Accepted if the judge's flaws don't rise and cost doesn't rise beyond the noise.
3. **A mandatory quality review:** before publishing is put to the user, the assembled document is reviewed against its brief whatever its citation score: a reader and coherence perspective over the whole document (problems across sections included), beside claims. Findings carry a location, evidence and severity: a quote from the document, or for something missing, the part of the brief or outline that asks for it. The existing reviewer, referee and writer loop fixes confirmed findings; rounds are bounded, the repaired version is reviewed again, and what remains unresolved is shown to the user at the approval. Accepted if the judge's flaws fall by more than the noise for at most about $0.20 a document.

The judge stays outside every run: used in production it would stop being a held-out measure.

## Results

**Round 0, baseline (2026-11-13, `r0-baseline`), $4.27 in all:**

| case | cost | minutes | strong model | audit | judge's flaws: broken text / citation form / unclear / repetition |
|---|---|---|---|---|---|
| scaling | $1.80 | 18 | $0.41 | 100% | 0 / 5 / 7 / 3 = 15 |
| sleep | $1.22 | 10 | $0.13 | 74% | 0 / 2 / 5 / 2 = 9 |
| reasoning | $1.25 | 10 | $0.21 | 63% | 1 / 3 / 7 / 0 = 11 (review cut short: the day's $10 limit) |

What it says before any change: broken text is nearly gone already (1 flaw in 35); the flaws are mostly content-level, unclear passages (19) and repetition (5), with citation form (10) a distant second, mostly the same source cited twice in a row. So structured output addresses about a third of the judged flaws, and a reader review the rest. The reasoning case's review was cut short by the machine-wide limit, so its audit and flaws are not a clean baseline; it is rerun in the next round. Variance is still unknown.

