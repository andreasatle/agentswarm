# Goals without a specification: design (draft, 2026-10-17)

## The question

The user's goal 12 starts with "set a goal". So far every project came with a written specification (a standard, a manual, an RFC); the self-supervised runs showed the swarm builds well from one (TOML 100%, YAML 97% then 98% hardened, JSONPath 99%, all tests hidden). Real goals rarely come with a specification. The question: **given only a goal of a line or two, can the swarm draft a specification good enough that the same loops (plan, derive checks, build, harden) reach the hidden tests nearly as well as with the written one, and what does a person's light touch add (answering a few questions, approving the draft)?**

## What the evidence says

| Finding | Source | Consequence for the design |
| --- | --- | --- |
| Generated tests a user confirms or rejects raise pass@1 from 48% to 70% with one query, 85% with up to five; a user model simulated from a reference solution makes this testable at scale | [TiCoder, 2022](https://arxiv.org/abs/2208.05950) | The person's input is a few cheap confirmations, not writing; the experiment simulates the person from a reference (the real spec). |
| Detecting ambiguity, asking clarifying questions and folding the answers into the requirement raised GPT-4 from 71% to 81% on MBPP-sanitized (human study), 62% to 70% averaged over five benchmarks (simulated users) | [ClarifyGPT, FSE 2024](https://arxiv.org/abs/2310.10996) | The drafter lists its open questions, each with the default it would choose; answers go into the draft. |
| A plan written before coding helps (self-planning, ADaPT, the swarm's own planner) | RESEARCH.md; [Self-planning, 2023](https://arxiv.org/abs/2303.06689) | The drafted specification is the plan's input, in sections, as the written ones were. |
| Models that judge their own output favor it | [Panickssery et al., 2024](https://arxiv.org/abs/2404.13076) | The drafter, the check writer and the referee stay of different families, as in the self-supervised runs. |

## The experiment

**JSONPath** (RFC 9535, 2024): the swarm reached 99% of the 715 hidden compliance tests from the RFC alone ($3.43). It is a recent standard the models know less well than TOML or YAML, so drafting from memory is a real test, and the hidden tests are ready.

| Arm | What the swarm gets | Measures |
| --- | --- | --- |
| A, written spec (done) | the RFC | 99%, $3.43 (2026-10-14) |
| B, goal only | "a JSONPath query engine in Python, as the 2024 standard defines it" | the drafted spec, then the build; hidden tests |
| C, goal and questions | as B, and the drafter's open questions answered by a simulated user who has read the RFC | the same, and how many questions it took |

Success: B or C close to A; the gap between B and C says what the person's answers are worth.

## The parts

| Part | What it does | Exists? |
| --- | --- | --- |
| Specifier (a team at a new first level, `spec`) | Drafts `docs/spec.md` from the goal: sections, each rule stated precisely with examples; lists open questions, each with its default | new; the planner's pattern |
| Questions and answers | Open questions go to a person (an escalation the user answers with `agentswarm answer`) or, in the experiment, to a simulated user reading the reference; the drafter revises with the answers | escalations exist; the simulated user is new |
| Approval | The draft is promoted to the build only when approved: by a person (a human gate), or automatically in the experiment | human gates exist |
| The build | The self-supervised project as before, with `docs/spec.md` as its spec | exists |

The simulated user answers only from the reference text, quoting it, and says "not specified" where the reference is silent, so it can't leak the hidden tests (it never sees them).

## Plan and cost

Building the specifier, the question loop and the simulated user: about a day, no paid calls (tests with fakes). Then two paid runs, B and C, about $5 each with a yes for each; arm A is already done.

## Risks and guards

- **The drafter writes what it knows, confidently wrong:** the hidden tests measure it; arm C shows whether questions catch it.
- **Answers that leak the tests:** the simulated user sees only the RFC, never the tests.
- **A drafted spec too vague to derive checks from:** the check writer's existing disputes and the referee apply; the specifier is told to state each rule with an example.
