# Self-supervised development: design (built 2026-10-12; for review)

## The question

How far does a swarm get on a real problem when nobody gives it expected answers, only the specification? The five problems so far were supervised: every visible test was an input with its correct output, a label. This experiment takes the labels away and measures what they were worth.

## The experiment

**TOML again, without its tests.** The same empty stub, the same spec and the same models as the supervised TOML run (100% of visible and held-out tests for $2.44), but no test in tests/acceptance/. All 679 official tests are held out: never shown, only scored at the start and end of each run.

Why TOML: a direct comparison with a known supervised result, so the only thing that changes is the labels. A small, precise spec. Strong self-derived checks exist for a parser (below).

**One honest caveat:** the TOML spec contains worked examples ("this document gives these values"). Those are a few labels. Keeping them makes the comparison clean (the supervised run had the same spec) and matches the semi-supervised recipe the evidence recommends (few labels, many derived checks). A stricter variant strips them, as was done for YAML and HTML5; it can follow if the first run succeeds.

**Success measure:** the hidden pass rate at the end, against the supervised 100%, and its cost against $2.44. Also measured: how many self-derived checks were wrong, and which hidden failures no derived check caught (sorted into kinds after the run, never fed back into the work).

## What the evidence says

| Finding | Source | Consequence for the design |
| --- | --- | --- |
| Model-written tests often assert wrong expected values: 28-58% of ChatGPT's assertions were incorrect, depending on the kind of code | [Unit test generation with generative AI, 2023](https://arxiv.org/abs/2312.10622); [Yuan et al., 2023](https://arxiv.org/abs/2305.04207) | Checks must not depend on the model computing expected outputs by hand. Prefer properties that need no expected value. A wrong check must be disputable. |
| The harder the input (the one most likely to find a bug), the less likely the model predicts its correct output | [UTGen, Prasad et al., 2025](https://arxiv.org/abs/2502.01619) | Same: round trips and metamorphic relations instead of hand-computed outputs for edge cases. |
| Tests written independently of the code, from the requirements, are more accurate and less biased than tests written next to it | [AgentCoder, 2023](https://arxiv.org/abs/2312.13010) | The check writer is a separate team, never sees the builder's code first, and uses a different model family. (The framework already does this for acceptance tests.) |
| Property-based tests can be written by models from API documentation, with characteristic failure modes (invalid generators, weak properties) | [Vikram, Lemieux, Padhye, 2023](https://arxiv.org/abs/2307.04346) | Properties come from the spec text; generators are checked to produce valid input. |
| Agreement between independent solutions picks correct code well, because wrong solutions rarely agree | [CodeT, ICLR 2023](https://arxiv.org/abs/2207.10397) | Differential testing works, as a second stage. |
| Independently written versions fail together more often than independence predicts | [Knight and Leveson, 1986](https://www.kth.se/social/files/564df871f2765419e306178d/KnightLeveson.pdf) | A majority is a hypothesis, not proof; disagreement is the stronger signal. Diversity (different model families) is required. |

## How the swarm works without labels

1. **Plan from the spec.** One top task: "implement TOML 1.0.0 as the spec states it". The planner splits it into ordered steps by spec section (strings, numbers, tables, arrays of tables, dates...), as it planned the CommonMark restructuring. Steps are building blocks, as now.

2. **Derived checks before each step.** A check writer (a variant of the acceptance team, on a different model family than the builder) reads the step and the spec section and writes tests that need no hand-computed answers:
   - **round trips:** generate Python values, write them as TOML with a small writer in the test file, parse them back, compare. The writer is test code by the check team, not part of the product, so the product's interface stays `loads` only, as in the supervised run;
   - **metamorphic relations:** equivalent documents parse to the same value (whitespace and comments added, keys quoted or bare, inline and standard tables, reordered tables);
   - **properties:** what the spec states for every input (duplicate keys are rejected; an integer with a leading zero is rejected; every parse result has only the spec's types);
   - **a few exact examples** where the spec itself states the result, quoted from it.

   The tests use a fixed random seed, so a failure is reproducible. They are protected like acceptance tests: the builder can't change them.

3. **Build and ratchet,** as now: the builder makes the step's checks pass without breaking earlier ones.

4. **Dispute a wrong check (new).** The builder's answer can name a check it believes contradicts the spec, quoting the spec. A referee, a third model family that wrote neither the check nor the code, reads the check, the quote and the spec section, and rules. Upheld: the check writer must fix or remove that test, and the step is retried. Rejected: the builder must meet it. Every dispute and ruling is on the board, so the rate of wrong checks is measured.

5. **Score, hidden.** At the start and end of every run, as now; the hidden failures are never shown to any agent or used to steer the work.

6. **Stage 2, only if needed: differential testing.** If the hidden score shows the swarm agreeing with itself while wrong, two builder teams on different model families write the parser separately, a comparer runs both on generated documents, and each disagreement becomes a finding settled by the referee against the spec. Built only on evidence from stage 1.

## Decisions made during implementation

- **The referee rules inside the builder's attempt,** not as a separate team: the builder's typed answer names the disputed tests, and the executor asks the referee before it runs the checks again. Independence comes from the referee's model family, not from a separate process, and nothing has to wait for another team.
- **An upheld dispute skips the test** (`@pytest.mark.skip` with the ruling as the reason, in a commit of its own) instead of sending it back to the check writer to rewrite. Simplest first; a rewrite step can follow if skipped tests leave gaps the hidden score shows.
- **Plain code rejects a dispute without a model call** when the test isn't an acceptance test or the quote isn't the spec's own words (whitespace aside), so a builder can't invent a rule.
- **Each test is disputed once;** at most 3 disputes are ruled per answer.
- **Model families:** Sonnet plans and writes the checks, Sol (and the local Gemma) builds, DeepSeek referees (examples/toml-self/project.yaml).

## First run (2026-10-13, $3.39 of $5): failed, and why

**Result:** the hidden score went from 7/679 to 0/679. Two causes, neither the parser's alone:

1. **The interface (my setup).** The hidden tests pass each file as bytes (some invalid files aren't UTF-8). The supervised stub's docstring said bytes are accepted, but neither the spec nor the north star does; the planner specified `loads(document: str)` with a TypeError for bytes, as tomllib does, so every hidden test failed before parsing. Measured with the bytes decoded first: 481/679, but that is 474 of 474 invalid documents rejected and only **7 of 205 valid documents** parsed: the parser rejects nearly everything it can't handle yet.
2. **Wrong checks that stood, because the referee ruled against its own reasoning.** 7 disputes, all by Sol; on reading them, the builder was right every time (a test demanding that `key = "value"` be rejected, left from the skeleton step; a random generator that turns "malformed" escapes into valid ones; a valid multi-line string the test rejects). DeepSeek upheld 2. In 4 of the 5 it rejected, its written reason agreed with the builder and its verdict didn't. The same test got opposite rulings in two steps. Two steps escalated on these tests, and 20 of the 26 planned steps never started.

**What it says:** the measurement works (the hidden score caught everything), and the dispute path is the right idea: the builder found real check errors. What failed is the referee's reliability and a few avoidable setup choices.

**Fixes before a second run (code, no paid calls):**
- The referee's answer gives its reasoning before its verdict (the verdict field was first, so the model committed before thinking; reasoning-then-answer is the established practice for structured judgements), and it is asked three times with the majority ruling (self-consistency; DeepSeek's three calls cost about a cent).
- A ruling holds project-wide: a test ruled once is not ruled again in another step, so rulings can't contradict each other.
- The check writer's prompt: checks must hold for the finished program, never assert that an input a later step will handle is rejected (the skeleton step's check froze a temporary limit, like the transitional tests the ratchet once protected).
- The interface in the north star and the stub: `loads` accepts str or UTF-8 bytes, invalid UTF-8 raises TOMLDecodeError.
- Planning one level deep (6 steps, as the planner first proposed), with the size question in shadow: 26 steps meant 26 check files to write and to get right.

## What gets built

| Part | Size | Exists? |
| --- | --- | --- |
| Seed with no visible tests, all held out (`import-toml --hide-all`) | small | the importer, a flag |
| Spec-driven start: one top task, planned into steps by the decomposer | small | yes, used for CommonMark |
| Check writer: the acceptance team with a prompt for derived checks (round trips, metamorphic, properties) | small | the team exists; a new prompt and answer type |
| Ratchet over the check writer's tests | none | yes |
| Disputes: a field in the builder's answer, a referee team, events for dispute and ruling, retry on an upheld one | medium | new |
| Measures: wrong checks, disputes, hidden score by run | small | scoring exists; a report line |
| Stage 2 differential testing | large | not now |

About a day of work, no paid model calls. Each part gets tests; PROMPTS.md shows the new prompts.

## Cost and guards

- One paid run, capped at **$5**, after a yes. The supervised TOML run cost $2.44; extra calls go to check writing and refereeing.
- Unchanged guards: a sandbox without network, protected checks, the budget, escalation to a person.
- A check writer that keeps writing wrong checks shows up as upheld disputes, and is reported, not tuned away.

## What it would tell us

- **Hidden score near 100%:** derived checks plus a spec are enough for a well-specified problem; labels mostly saved cost.
- **A clear gap:** which kinds of rules self-derived checks miss, which is exactly what a project without a test suite must budget a person or a few labels for.
- Either way, the measured rate of wrong model-written checks and the cost of refereeing them, numbers the literature lacks for checks used to steer agents.
