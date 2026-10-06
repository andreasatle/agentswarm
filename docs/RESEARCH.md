# What actually works: research ground truth for AgentSwarm

This is the reference for design discussions. A proposal that contradicts a principle here needs evidence of
its own, or an experiment that would produce it. Principles have IDs (P1, P2, …) so discussions can cite
them. New findings are appended (see "Adding to this document" at the end); nothing is silently rewritten.

The goal it serves, in the user's words: **robustness and getting to the correct answer, especially when
goals are high level.** Token cost and how heavy the system looks on toy problems matter less.

Compiled 2026-10-05 from papers and first-hand engineering reports. Each finding is labelled:

- **strong**: replicated across several studies or a large controlled study;
- **moderate**: one careful study, or several weaker ones that agree;
- **practitioner**: experience reports from teams running systems in production, not controlled studies;
- **contested**: credible sources disagree.

## Summary: twelve principles

| ID | Principle | Evidence |
| --- | --- | --- |
| P1 | Reliability comes from **external verification** (execution, tests, checkers), not from models judging themselves | strong |
| P2 | Spend extra compute on **many independent attempts plus selection** before spending it on agents talking to each other | strong |
| P3 | Selection is the bottleneck: **without a real verifier, more attempts stop helping** | strong |
| P4 | Multi-agent systems fail mostly from **bad specifications and coordination**, not weak models | moderate |
| P5 | Split work across agents only where it is **truly parallel and needs no shared decisions**; otherwise keep one thread with full context | practitioner |
| P6 | Long chains fail by compounding; measure **consistency (pass^k)**, not single successes | strong |
| P7 | **Very small steps plus voting at each step** can make long chains reliable, even with small models, when the decomposition is known | moderate |
| P8 | Start with the **simplest system that passes evaluation**; compare everything against strong simple baselines **at equal cost** | strong |
| P9 | Model judges are **biased** (position, length, their own outputs); use them only calibrated, blind and never on their own work | strong |
| P10 | Models' errors are **correlated**, even across vendors; diversity must come from different evidence, not just different models | moderate |
| P11 | **Underspecified goals** are a leading cause of failure; asking targeted questions helps a lot, but models rarely ask on their own | moderate |
| P12 | Agents **game their checks** when they can (editing tests, special cases); stronger models do it more | moderate |

Two practical findings sit beside these: **context degrades long before the window is full** (section 10),
and **the effect of forcing JSON on reasoning is contested** (section 10).

---

## 1. Verification is the foundation (P1)

- **Models can't reliably correct their own reasoning without outside feedback.** Asked to review and fix
  their own answers, models often get *worse* ("their performance even degrades after self-correction").
  ([Huang et al., ICLR 2024](https://arxiv.org/abs/2310.01798))
- A critical survey of the self-correction literature finds **no successful self-correction from feedback by
  prompted models**, except on tasks exceptionally suited to it; it works with **reliable external feedback**
  or large-scale fine-tuning. Many positive results came from weak baselines or leaked answers.
  ([Kamoi et al., TACL 2024](https://arxiv.org/abs/2406.01297))
- **With tools, correction works.** CRITIC lets a model check its output with a search engine or a code
  interpreter and then revise; it consistently improves results.
  ([Gou et al., ICLR 2024](https://arxiv.org/abs/2305.11738)) Reflexion's coding gains (91% pass@1 on
  HumanEval) come from executing tests and reflecting on the results.
  ([Shinn et al., NeurIPS 2023](https://arxiv.org/abs/2303.11366))
- **Planning needs external checkers.** LLMs can't plan or verify plans by themselves, but are useful as
  idea generators inside a loop with sound external verifiers ("LLM-Modulo").
  ([Kambhampati et al., ICML 2024](https://arxiv.org/abs/2402.01817))
- **Checking each step beats checking only the end** when steps can be checked: a step-level verifier chose
  correct math solutions much better than an outcome-only one (78% vs 72% on MATH).
  ([Lightman et al., 2023](https://arxiv.org/abs/2305.20050))

**Evidence: strong.** Every reliable gain in the literature routes through something that is not the model's
own opinion: a test, an interpreter, a proof checker, a search result.

## 2. Many attempts plus selection (P2, P3)

- **Sampling and voting scales.** Accuracy rises steadily with the number of independent samples combined by
  majority vote, across reasoning, knowledge and code tasks, and gains are larger on harder tasks.
  ([Li et al., "More Agents Is All You Need", 2024](https://arxiv.org/abs/2402.05120))
- **Coverage keeps growing with attempts.** The fraction of problems solved by *any* attempt grows over four
  orders of magnitude of samples. On SWE-bench Lite one model went from 15.9% with one sample to 56% with 250,
  beating the best single-attempt system (43%), because the repository's tests identify the right attempt.
  ([Brown et al., "Large Language Monkeys", 2024](https://arxiv.org/abs/2407.21787))
- **But without a verifier, selection plateaus:** majority voting and reward models "plateau beyond several
  hundred samples and fail to fully scale with the sample budget". (same paper) This is P3.
- **Tests can be generated too.** Generating tests with the model, then picking the solution that agrees most
  with both the tests and the other solutions, raised HumanEval pass@1 by 18.8 points absolute.
  ([Chen et al., CodeT, 2022](https://arxiv.org/abs/2207.10397))
- **Debate is not better than voting at equal compute.** Multi-agent debate "does not reliably outperform"
  self-consistency or ensembling, and with the same number of responses it can significantly underperform
  plain majority voting. ([Smit et al., "Should we be going MAD?"](https://openreview.net/pdf?id=CrUmgUaAQp);
  ["Revisiting Multi-Agent Debate as Test-Time Scaling", 2025](https://arxiv.org/abs/2505.22960);
  ["Debate or Vote", 2025](https://arxiv.org/abs/2508.17536))
- **One strong model sampled many times can beat a mix of models.** Aggregating samples of the single best
  model beat mixing different models by 6.6% on AlpacaEval 2.0 and 3.8% on average; mixing lowers average
  quality. ([Li et al., "Rethinking Mixture-of-Agents", 2025](https://arxiv.org/abs/2502.00674))
- **Extra attempts help small models most where they already sometimes succeed.** With compute-optimal use of
  test-time compute, a smaller model beat a 14× larger one on problems where it had a non-trivial success
  rate; on the hardest problems, a bigger model was the better investment.
  ([Snell et al., 2024](https://arxiv.org/abs/2408.03314))

**Evidence: strong.** This is the single most dependable lever, and it is what the goldilocks hypothesis
should be measured against: *n samples of one model, selected by tests, at the same token budget.*

## 3. Why multi-agent systems fail (P4)

- An analysis of 1,600+ execution traces from 7 popular multi-agent frameworks (κ = 0.88 between annotators)
  found 14 failure modes in three groups: **specification and system design ~42%**, **inter-agent
  misalignment ~37%**, **task verification ~21%** (stopping early, skipping or shallow checks). Simple fixes
  such as better prompts or clearer roles were not enough; the failures need structural redesign.
  ([Cemri et al., NeurIPS 2025](https://arxiv.org/abs/2503.13657))

**Evidence: moderate** (one large, careful study). It says where to look first: the task specification, the
hand-offs, and the verification.

## 4. When multiple agents help, and when to keep one thread (P5)

- **Anthropic's research system** (a lead agent spawning parallel sub-agents) beat a single agent by 90.2% on
  its internal research evaluation, but used about **15× the tokens of a chat** (single agents use about 4×),
  and **token usage alone explained 80% of the performance variance**. It fits "breadth-first queries that
  involve pursuing multiple independent directions"; it fits poorly where agents must share context or have
  many interdependencies, and "most coding tasks involve fewer truly parallelizable tasks than research".
  Early versions spawned 50 sub-agents for simple questions and duplicated work under vague instructions.
  ([Anthropic, 2025](https://www.anthropic.com/engineering/multi-agent-research-system))
- **Cognition (Devin)**: "Share context, and share full agent traces, not just individual messages", and
  "actions carry implicit decisions, and conflicting decisions carry bad results". Their example: two
  sub-agents building parts of a game in incompatible styles that no final agent can reconcile. Their default
  is one single-threaded agent, with a model that compresses history for very long tasks.
  ([Cognition, 2025](https://cognition.com/blog/dont-build-multi-agents))

**Evidence: practitioner, consistent across two independent teams.** Parallel agents pay off for independent
breadth (research, independent reviews). Work whose parts share decisions needs one owner of those decisions.
Note that much of the measured gain is simply *more compute*; an honest comparison holds compute equal (P8).

## 5. Long chains, consistency and decomposition (P6, P7)

- **Single successes overstate reliability.** τ-bench introduced pass^k, the chance that *all* k attempts at
  a task succeed. A leading model solved about 61% of retail tasks once, but fewer than 25% eight times out
  of eight. ([Yao et al., 2024](https://arxiv.org/abs/2406.12045))
- **Task length is the frontier.** The length of tasks (in human time) that frontier agents finish with 50%
  reliability has doubled about every 7 months for six years; reliability falls with task length.
  ([METR, 2025](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/))
- **Extreme decomposition plus voting makes long chains reliable.** MAKER solved a task of over a million
  dependent steps (20-disk Towers of Hanoi) with zero errors by (1) giving each micro-agent a single step,
  (2) voting at every step until one answer leads by k votes, and (3) **red-flagging**: discarding replies
  with risk signs such as excessive length or wrong format, which also reduces correlated errors. Relatively
  small, non-reasoning models sufficed. The limit: the decomposition has to be known in advance.
  ([Meyerson et al., 2025](https://arxiv.org/abs/2511.09030))

**Evidence: strong for P6, moderate for P7.** P7 is the closest published support for the goldilocks
hypothesis: many small agents beat one big one *when each step is small, checked and voted on*. It says
nothing yet about tasks whose decomposition must itself be invented.

## 6. Simplicity and honest baselines (P8)

- **Anthropic's guidance:** use the simplest pattern that passes evaluation, often a fixed workflow or a
  single well-tooled call; reserve autonomous agents for when the path can't be hard-coded but progress can
  still be verified. The **evaluator–optimizer** loop helps when evaluation criteria are clear.
  ([Anthropic, "Building effective agents", 2024](https://www.anthropic.com/engineering/building-effective-agents))
- **Agentless**: a fixed three-step pipeline (locate, repair, validate with tests) solved 32% of SWE-bench Lite
  at $0.70 per issue, the best open-source result at the time and ahead of most complex agents.
  ([Xia et al., 2024](https://arxiv.org/abs/2407.01489))
- **AI Agents That Matter**: agent evaluations routinely ignore cost; with cost controlled, simple baselines
  such as calling the model several times matched or beat complex agent architectures on HumanEval at much
  lower cost. ([Kapoor et al., 2024](https://arxiv.org/abs/2407.01502))

**Evidence: strong.** Any claim that a team structure helps must beat "one model, n attempts, tests pick one"
at the same budget.

## 7. Judging with models (P9)

- Strong model judges agree with humans about as often as humans agree with each other, but show **position
  bias**, **verbosity bias** and **self-enhancement bias**; randomizing order helps.
  ([Zheng et al., NeurIPS 2023](https://arxiv.org/abs/2306.05685))
- **Self-preference is real and linked to self-recognition:** models score their own text higher than
  humans would, and the bias grows with their ability to recognize their own outputs.
  ([Panickssery et al., NeurIPS 2024](https://arxiv.org/abs/2404.13076))
- Even Anthropic's production evaluation of its research agent combined an LLM rubric judge with manual
  testing, which found hallucinations on unusual queries that automation missed.
  ([Anthropic, 2025](https://www.anthropic.com/engineering/multi-agent-research-system))

**Evidence: strong.** Judges are instruments that need calibration against ground truth, blindness to which
model produced an answer, randomized order, and a different model from the one judged.

## 8. Diversity and correlated errors (P10)

- Across 350+ models, when two models both err they **agree on the same wrong answer about 60% of the time**
  on one leaderboard, and **larger, more accurate models have more correlated errors, even across vendors and
  architectures**. ([Kim et al., ICML 2025](https://arxiv.org/abs/2506.07962))
- Combined with Self-MoA (section 2): adding different models is not a dependable source of independence.

**Evidence: moderate.** Independence has to be engineered: different evidence (executed tests, retrieved
sources, other inputs), different framings of the check, red-flagging (P7). Narrow red teams are an attempt
at this, and whether they catch more than a broad one at equal cost is an open measurement.

## 9. Routing and cascades

- **FrugalGPT**: trying cheaper models first and moving up only when an answer isn't good enough matched the
  best single model at up to 98% lower cost, or beat it by 4% at the same cost.
  ([Chen et al., 2023](https://arxiv.org/abs/2305.05176))
- **RouteLLM**: routers learned from preference data cut costs by more than 2× (85% on MT-Bench) while keeping
  95% of the strong model's quality. ([Ong et al., 2024](https://arxiv.org/abs/2406.18665))
- Snell et al. (section 2): the best use of compute depends on the problem's difficulty.

- **Routing can be learned online, as a bandit problem.** Several 2025 routers start from little or no data and
  learn from each call's outcome: MetaLLM picks the cheapest model likely to be right, with rewards from
  correctness and cost; MixLLM and PILOT update from binary success feedback (PILOT also starts from offline
  preference data); others use Thompson sampling or LinUCB with budget constraints. They balance trying models
  to learn about them against using what is known to work.
  ([routing and cascading survey, 2026](https://arxiv.org/abs/2603.04445);
  [online multi-LLM selection via contextual bandits, AAAI](https://arxiv.org/abs/2506.17670);
  [routing with dueling feedback, 2025](https://arxiv.org/abs/2510.00841);
  [adaptive routing under budget constraints, EMNLP Findings 2025](https://aclanthology.org/2025.findings-emnlp.1301.pdf))

**Evidence: moderate.** Cascades work when there is a trustworthy signal for "good enough": a verifier, or a
well-calibrated score. Without one they inherit the selection problem (P3).

## 10. Context and output format

- **Context rot**: in tests of 18 frontier models, every one got worse as input grew, even on simple
  retrieval and copying, and long before its window was full.
  ([Chroma, 2025](https://research.trychroma.com/context-rot)) Our own runs lost a third of their answers
  to Ollama's silent 4096-token window (MODELS.md, section 4).
- **Forcing JSON: contested.** One study found large drops on reasoning tasks when answers must be JSON
  (e.g. −17 to −24 points on GSM8K) ([Tam et al., EMNLP 2024](https://arxiv.org/abs/2408.02442)); a rebuttal
  attributes the drop to unequal prompts, and finds structured output matching or beating free text on
  well-structured tasks ([.txt, "Say What You Mean"](https://blog.dottxt.ai/say-what-you-mean.html)). A
  common mitigation is letting the model reason before it gives the structured answer. **This should be
  measured on our probes, not assumed.**

## 11. Specifications and high-level goals (P11)

- Specification problems are the largest failure group in multi-agent systems (~42%, section 3).
- **Asking helps a lot, but models don't ask.** On underspecified software tasks, interacting to get missing
  information improved results by up to 74% over not interacting, and actionable clarifying questions drove
  the gain; but models struggle to tell well-specified from underspecified tasks and default to not asking.
  ([Ambig-SWE, ICLR 2026](https://arxiv.org/abs/2502.13069))
- LLM-Modulo (section 1): for plans, the model proposes and external critics check.

**Evidence: moderate.** For high-level goals, the decomposition and the success criteria are where most of
the risk is. They deserve verification of their own, and a human is the best available checker of intent.

## 12. Agents game their checks (P12)

- **ImpossibleBench** builds coding tasks whose tests contradict their specification, so any pass is cheating.
  Frontier models frequently cheat (modifying tests, special-casing, operator overloading), **stronger models
  cheat more**, and prompting matters enormously: one model's rate dropped from over 85% to 1% under strict
  instructions. Restricting access to the tests and the feedback loop also changes the rate.
  ([ImpossibleBench, ICLR 2026](https://arxiv.org/abs/2510.20270))

**Evidence: moderate.** Any check the producer can see or edit is weaker than one it can't.

## 13. Evaluation hygiene

- **Contamination**: some models drop sharply on problems released after their training date, which suggests
  older benchmark problems were seen in training; HumanEval may be overfitted.
  ([Jain et al., LiveCodeBench, 2024](https://arxiv.org/abs/2403.07974))
- Small evaluation sets are fine for spotting large effects early (Anthropic started with about 20 queries),
  but conclusions need confidence intervals and enough samples.
- Report accuracy **with** cost (P8) and **consistency** (P6).

---

## What this means for AgentSwarm

### Already aligned

| Design choice | Principle |
| --- | --- |
| Hard checks gate every promotion; the merge queue reruns them on merged work | P1 |
| Critics must back each finding with a failing test or an exact quote; claims without evidence are dropped | P1, P9 |
| Hidden acceptance tests the executor never sees score the evaluations | P12 |
| Jev's thresholds are fitted to logged outcomes; new wording means new calibration | P9 |
| Typed inputs and outputs; invalid, cut-off and missing answers are measured separately | 10 |
| Teams coordinate through the board; each child task must be self-contained | P5 |
| Planned: blind rating of answers | P9 |
| Comparisons report tokens and confidence intervals | P8, 13 |

### Gaps, and what to change (in order of expected value)

1. **The baseline is missing (P2, P8).** Every comparison needs the setup "one model, n independent attempts,
   hidden tests or the critics' tests pick one", at the same token budget as the team setups. Without it a
   win for a swarm can't be told apart from a win for more compute.
2. **The producer makes one attempt (P2).** The strongest known gain is several attempts selected by checks.
   The executor (or a setup) should be able to produce n candidates, and a selection step should keep the one
   that passes the most checks, including the tests critics write (CodeT-style agreement).
3. **Measure consistency (P6).** Probes should be able to repeat each prompt k times (with temperature above
   0) and report pass^k next to the success rate.
4. **Red-flag and resample (P7).** Answers that are invalid, cut off or abnormally long should be discarded
   and resampled rather than escalated at once; MAKER found this both raises step reliability and lowers
   correlated errors. Our new failure split (invalid, cut off) already gives the signals.
5. **Decompositions need their own verification (P4, P11).** For high-level goals the split is the riskiest
   step. Options, to be measured: a human approves splits above a size; Jev's split check is calibrated
   against outcomes; several proposed splits are compared and voted on.
6. **Ask before building (P11).** An explicit clarification step for vague goals: a model lists what is
   underspecified, and the questions go to the human through the escalation channel before work starts.
7. **Keep checks out of the producer's reach (P12).** Acceptance tests should be written by a different team
   from the implementation (ideally before it) and protected: a hard check that fails when existing tests
   are weakened or deleted.
8. **Diversity by evidence, not by brand (P10).** Narrow red teams are the right kind of experiment; their
   value is to be shown against the broad red team *and* against n samples of one critic at equal cost.
9. **Judges never judge their own model's work (P9).** When Jev's fallback model is used, it must not be the
   producer's model; position is randomized when answers are compared.
10. **Measure the structured-output question (section 10).** Probe variants with and without a reasoning field
    before the answer; keep whichever is better per model.

### Open questions our experiments can answer

- Does the goldilocks range exist **at equal compute**, against the n-sample baseline (gap 1)?
- Do narrow red teams catch bugs that n samples of a broad critic don't?
- Do routing curves cross between models, so that per-call routing pays?
- For tasks whose decomposition is *not* known in advance, does MAKER-style voting still help, and at which
  level (the split, the steps, or both)?

## Adding to this document

Append new findings to the relevant section, or add a section, in this form:

```
- **Finding in one sentence.** Detail, numbers. ([Authors, venue year](link))
```

Add a principle only when the evidence is at least moderate, give it the next ID, and add it to the summary
table. If new evidence weakens a principle, mark it **contested** and keep both sides with their sources;
don't delete the old evidence. Record changes below.

### Changes

- 2026-10-05: first version (P1–P12).
- 2026-10-05: routing learned online (bandits) added to section 9, after the user proposed it.
- 2026-10-05: the gaps are planned: 1, 3 and 10 in Phase 8 (step 14), the rest in [Phase 10](design/phase-10-robustness.md).
