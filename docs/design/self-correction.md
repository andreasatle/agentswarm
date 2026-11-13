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

1. **Round 0, baseline:** the code as of 2026-11-13.
2. **Typed sentences:** the writer returns its section as a list of sentences, each with its citations, instead of free text; plain code lays them out. If it holds, the sentence splitter and the citation-moving patches are removed (goal 15). Accepted if flaws don't rise and cost doesn't rise beyond the noise.
3. **A reader critic:** the review's existing `reader` perspective (quote-backed findings, a referee of another model family, the writer fixing confirmed ones) runs beside `claims`, on a cheap model. Accepted if the judge's flaws fall by more than the noise for at most about $0.20 a document.

## Results

(to come, one line per round)
