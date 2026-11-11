# Checking work without an answer key: verification when there is no ground truth

*A literature study written by Claude (Anthropic) on 11 November 2026 for the AgentSwarm project, from papers read on the web; the agent swarm did not write it. Where the report argues rather than reports, it says so.*

## The question, and the short answer

An agent system is only as good as its ability to tell good work from bad. For code with a test suite that is easy; for most real goals (a document, an analysis, a design, code without tests) there is no answer key, and the check is itself a judgement, often by another model. The question is what the evidence says about checking work when there is no ground truth: what kinds of checks can be trusted, how far, how to combine weak ones, and how to know how good a check is without labels.

The short answer:

1. **Verification is the lever.** Models cannot reliably correct themselves without an outside signal (Huang et al., 2024; Stechly et al., 2025), and a checker that passes wrong answers puts a ceiling on what any amount of generation can reach (Stroebl et al., 2024). Self-improvement is governed by the gap between how well a model generates and how well it verifies (Song et al., 2025).
2. **Checks form a ladder of strength.** From strongest to weakest: a sound external checker (tests, solvers); a check of each claim against a named source (entailment); consistency checks that need no answer (agreement between independent attempts, metamorphic relations); a model's judgement; and last, a model judging its own work. The single most useful move is to push a check up the ladder: turn "is this good?" into many small questions that a source or a test can answer.
3. **Model judges are useful but biased and correlated.** A strong judge agrees with people about as often as people agree with each other, around 80% (Zheng et al., 2023), but prefers the first answer it sees, longer answers, and its own writing (Panickssery et al., 2024). Different models make the same mistakes (Kim et al., 2025). A panel of small judges from different families beats one large judge, at a seventh of the cost (Verga et al., 2024).
4. **Weak checks can be combined, and their accuracy estimated, without labels,** as long as their errors are not too correlated (Dawid and Skene, 1979; Saad-Falcon et al., 2025). A small labelled calibration set then buys a statistical guarantee: trust a verdict only when it is confident enough, and escalate the rest (Jung et al., 2025), or keep only the claims that pass a calibrated bar (Mohri and Hashimoto, 2024).
5. **Optimising against a checker eventually games it.** Pushing harder against a proxy first improves the real target, then makes it worse (Gao et al., 2023). Checks the producer never sees, and checks of a different kind, are the defence.

## 1. Why verification is the lever

**Self-correction needs an outside signal.** Huang et al. (2024, ICLR) found that models asked to review and fix their own reasoning, with no external feedback, do not improve and sometimes get worse. Stechly, Valmeekam and Kambhampati (2025, ICLR) tested GPT-4 on graph colouring, Game of 24 and planning: self-critique collapsed performance, while a sound external verifier, even one that only says "wrong" and re-prompts, gave large gains. Kamoi et al. (2024, TACL), surveying the self-correction literature, found no convincing case of prompted self-correction from the model's own feedback except on tasks suited to it; it works with reliable external feedback.

**The checker sets the ceiling.** Stroebl, Kapoor and Narayanan (2024) showed that resampling until a solution passes the tests cannot push the error below the tests' false-positive rate, whatever the budget, and that weaker models produce more false positives, so their ceiling is lower; the optimal number of samples is often under ten. Brown et al. (2024) saw the other side: where a perfect checker exists, the fraction of problems solved by some attempt keeps growing with attempts, and without one, majority voting and reward models plateau.

**The gap between generating and verifying governs improvement.** Song et al. (2025, ICLR) formalised self-improvement as generate, verify, filter and distil, and found that it is governed by the *generation-verification gap*, which grows with pre-training compute: larger models are relatively better at checking than at producing. Process supervision, checking each step rather than only the final answer, made a stronger verifier for maths than outcome supervision (Lightman et al., 2024: 78% of a MATH subset solved with a step-level reward model).

**Lesson:** spend on the checker before spending on the generator; and never let a model's opinion of its own work count as a check.

## 2. A ladder of checks

*The ladder is this report's organising device, built from the evidence below.*

| Rung | What answers the question | Examples | Strength and failure mode |
|---|---|---|---|
| 1. Sound external check | A program or the world | Unit tests, a compiler, a solver, a schema validator, running the code | Strongest; fails only where its coverage is incomplete (false positives, Stroebl et al.) |
| 2. Grounded claim check | A named source | Is this sentence supported by this passage? (MiniCheck, SAFE, FActScore, citation checks) | Strong where the claim is atomic and the source is right; fails on paraphrase, missing context, wrong source |
| 3. Consistency without an answer | Agreement between independent attempts or views | Majority voting, semantic entropy, differential testing, metamorphic relations | Needs no oracle; fails when attempts share a mistake |
| 4. Model judgement | Another model's opinion | LLM-as-judge, rubric scoring, pairwise preference | Flexible; biased and correlated, needs calibration |
| 5. Self-judgement | The producer's own opinion | Self-critique, self-refine | Weakest; often negative (section 1) |

The software-testing literature met the same problem decades ago as the *test oracle problem*: how to decide whether an output is correct when no oracle exists (Barr et al., 2015, survey). Its answers map directly onto the rungs: specifications and contracts (rung 1), partial and pseudo oracles such as metamorphic relations, where a known relation between outputs of related inputs must hold even when the outputs themselves are unknown (rung 3), and, when nothing else is adequate, the human (rung 4 with a person as judge).

**Pushing checks up the ladder.** The most effective systems in the literature do not ask a judge "is this document accurate?"; they decompose it into atomic claims and check each against a source. SAFE (Wei et al., 2024, Google DeepMind) splits a long answer into individual facts and checks each with search queries; across about 16,000 facts it agreed with crowdsourced annotators 72% of the time, and when they disagreed SAFE was judged right in 76% of a sample of 100 cases, at more than 20 times lower cost than the annotators. MiniCheck (Tang, Laban and Durrett, 2024, EMNLP) trained a 770M-parameter checker on synthetic hard examples to decide whether a sentence is supported by a document; it matched GPT-4 on the LLM-AggreFact benchmark at about 400 times lower cost. Grounded claim checking is one of the clearest cases where a small model does a large one's job.

## 3. Model judges: what they get right and wrong

**Agreement.** Zheng et al. (2023, MT-Bench and Chatbot Arena) found that GPT-4 as a judge agreed with human preferences over 80% of the time, about as often as humans agree with each other.

**Biases.** The same study documented *position bias* (preferring the first of two answers), *verbosity bias* (preferring longer answers, even padded ones) and *self-enhancement* (preferring one's own answers). Panickssery, Bowman and Feng (2024, NeurIPS) showed self-preference is real and causal: models recognise their own text better than chance, and fine-tuning that increases self-recognition increases self-preference linearly. A producer and a judge from the same model, or the same family, are not independent.

**Correlation.** Kim et al. (2025, ICML), across more than 350 models, found that when two models are both wrong they give the same wrong answer about 60% of the time on one leaderboard, more so for models from the same provider and for more accurate models. A second opinion from a similar model is worth much less than a second opinion.

**Juries.** Verga et al. (2024, Cohere) replaced one large judge with a panel of three smaller judges from different families (Command R, Claude 3 Haiku, GPT-3.5): the panel agreed better with human judgements, showed less bias toward any one model's outputs, and cost over seven times less. They also report that a single large judge was sensitive to small prompt changes.

**Calibration.** Kadavath et al. (2022, Anthropic) found that large models are reasonably well calibrated when asked for the probability that an answer is true, especially after seeing several of their own samples, but that knowing whether they know transfers less well to new tasks. A judge's confidence is informative, but must be checked on the task at hand.

**Lesson:** use juries of different families, randomise answer order, control length, never let a model judge its own family's work alone, and measure the judge against people on a sample before trusting it.

## 4. Combining weak checks without labels

When several imperfect checks rate the same items, their accuracies can be estimated *from their agreement pattern alone*. Dawid and Skene (1979) did this for doctors' diagnoses: with an expectation-maximisation algorithm over many items, each rater's error rates and each item's likely true label are estimated jointly, with no answer key. Data programming and Snorkel (Ratner et al., 2016 and 2017) applied the same idea to labelling functions in machine learning: many noisy heuristics, combined by a model of their accuracies, give labels good enough to train on.

Weaver (Saad-Falcon et al., 2025, NeurIPS) brought this to verifying model outputs: it combines many weak verifiers (reward models, judges) using weak supervision, without ground-truth labels. With Llama 3.3 70B generating, it reached o3-mini-level accuracy on reasoning benchmarks, clearly better than majority voting; a 400M model distilled from Weaver's combined scores kept about 98% of its accuracy at a tiny fraction of the verification cost.

**The independence caveat.** All of these methods assume the checks' errors are not too correlated. The classic warning is from software: Knight and Leveson (1986) had 27 versions of one program written independently from the same specification and ran a million tests; each version was very reliable, but far more tests failed in several versions at once than independence predicts, because programmers make related mistakes. Kim et al. (2025) found the same for language models. Combined checks are worth most when they are *of different kinds* (a test, a source check, a judge) rather than many of the same kind.

**Lesson:** several weak checks of different kinds, combined with a model of their accuracies, beat any one of them; and that model can be fitted without labels.

## 5. Guarantees from a small calibration set

Two results show how a few labelled examples turn a judge into something with a statistical guarantee.

**Trust or escalate.** Jung, Brahman and Choi (2025, ICLR) let a judge estimate how likely a person would agree with its verdict, set a confidence threshold on a small calibration set with a fixed-sequence test, and accept only verdicts above it; the rest go to a stronger judge, and finally to a person. The result is a guarantee on agreement with humans for the verdicts it accepts. Starting from a cheap judge (Mistral-7B) and escalating, they guaranteed over 80% human agreement on a Chatbot Arena subset with nearly 80% of items covered, where GPT-4 alone almost never reached 80%.

**Conformal factuality.** Mohri and Hashimoto (2024, ICML) treat a generated text as a set of claims, score each claim's reliability, and drop the least reliable ones until a conformal threshold, set on a small labelled sample, guarantees that the remaining text is fully correct with a chosen probability (80–90% in their experiments) while keeping most of the content. The output becomes less specific rather than wrong.

**Lesson:** a small set of labelled examples (tens to hundreds) is worth more than any amount of unlabelled judging, because it converts confidence into a guarantee; and "say less, but only what holds" is a principled way to reach a quality bar.

## 6. Arguing and attacking: debate, red teams, legibility

**Debate helps when claims can be checked against evidence.** Khan et al. (2024, ICML) gave two expert models a text the judge could not read, and had them argue for opposite answers with quotes from it; quotes were verified against the text. Non-expert judges reached 88% accuracy (humans) and 76% (models) with debate, against 60% and 48% without, and making debaters more persuasive *raised* judge accuracy. With a single consultant arguing one side, more persuasion lowered it. By contrast, Choi, Zhu and Li (2025, NeurIPS) found that debate *without* external evidence adds nothing in expectation beyond majority voting. The difference is the verified quote: argument helps when it points the judge at evidence the judge can check.

**Make outputs checkable.** Kirchner et al. (2024, OpenAI) trained a "helpful" prover to write solutions a small verifier accepts, a "sneaky" prover to write wrong solutions that fool it, and the verifier on both, in rounds. The helpful prover's solutions became more *legible*, easier for a small model, and for time-limited people, to check, at a small cost in raw accuracy. Producers can be shaped to be checkable, not only correct.

**Red teams find what checks miss.** In the same spirit, adversaries whose job is to make a product fail, and who must demonstrate the failure (a failing input, a counterexample, a quote that contradicts a claim), turn the open-ended question "is anything wrong?" into concrete rung-1 or rung-2 checks that then stay in the suite.

## 7. Goodhart: when the checker becomes the target

Gao, Schulman and Hilton (2023, ICML) measured what happens when a policy is optimised against a proxy reward model and scored by a gold one: the gold score first rises, then falls as optimisation pushes further, both for best-of-n selection and for reinforcement learning, with the turning point predictable from the proxy's size. Stroebl et al. (2024) is the same effect in testing: weak tests pass wrong code, and more sampling finds more of it. And Panickssery et al. (2024) is the same effect in judging: a producer that writes like the judge is preferred by it.

The defences in the literature are consistent: **held-out checks** the producer never sees and cannot change; checks of **a different kind** from the one being optimised against; **bounded optimisation** (few samples, early stopping against the proxy); and **refreshing** the checks (new red-team findings, new calibration labels) as the producer improves.

## 8. A verification stack for agents

*This section is a design proposal built from the evidence above.*

1. **Decompose before judging.** Turn every quality question into atomic claims or properties with a named evidence source: a test, a passage, a computed number. Ask a judge only what cannot be decomposed.
2. **Use the cheapest sound check first, and climb only on doubt.** A test or schema check, then a small grounded checker (MiniCheck-sized), then a jury, then a person: the cascade of Jung et al., applied to verification.
3. **Use juries of different families, never the producer's own family alone.** Randomise order, cap length, and record each juror's verdict separately.
4. **Estimate checker accuracy without labels, continuously.** Fit a Dawid–Skene-style model to the verdicts already being collected; it gives each checker's error rates and flags items where the checkers disagree in an informative way.
5. **Keep a small labelled calibration set per kind of check.** Tens to hundreds of items labelled by a person, refreshed now and then; use it to set thresholds with a guarantee and to measure each checker's false-positive rate, which is the ceiling on everything downstream.
6. **Report coverage and guaranteed accuracy, not one score.** "92% of claims accepted with at least 90% guaranteed correctness; 8% escalated" says more than "90% supported".
7. **Make producers checkable.** Require quotes, citations, step-by-step derivations and small diffs; reject outputs that cannot be checked, as MAKER rejects confused answers before voting.
8. **Keep held-out checks and adversaries.** The producer never sees some checks; red teams keep adding demonstrated failures to the suite.

In the multiscale reading of the companion essay (*Can many small models compete with one large model?*), this stack is the compression operator: it is what turns many cheap micro results into data the macro level can trust, and its false-positive rate is the floor under the whole system's error.

## 9. What this means for AgentSwarm (briefly)

AgentSwarm already uses several rungs: tests and held-out tests for software, a claim-by-claim check of every cited statement against its source, a referee of another model family, and red teams that must demonstrate failures. Its own measurements fit the literature: the claims checker caught 19 of 23 problems an independent audit found, the referee agreed with that audit about three times in four, and the citation audit stalls at roughly 80–90% supported after review, which looks like a checker ceiling rather than a writer ceiling. The literature suggests three cheap next steps, none needing paid runs to start: label a small calibration set of claims by hand and measure the checker's and referee's false-positive rates; fit a Dawid–Skene model to the verdicts already on the boards to estimate each checker's accuracy and their correlation; and report document quality as coverage plus guaranteed accuracy.

## 10. Open questions

- **How much correlation is too much?** All label-free combination methods assume errors are not too dependent; there is no good way yet to measure dependence among model checkers without labels.
- **Checks for goals that do not decompose.** Coherence, usefulness and design quality resist atomic claims; juries and calibration help, but there is no rung-1 or rung-2 check for them.
- **Checkers that keep up.** As producers improve, their mistakes become subtler and checkers trained on yesterday's errors miss them; prover-verifier training and red teams are partial answers.

## References

- Barr, E. T., Harman, M., McMinn, P., Shahbaz, M. and Yoo, S. (2015). The Oracle Problem in Software Testing: A Survey. IEEE Transactions on Software Engineering 41(5), 507–525.
- Brown, B. et al. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. arXiv:2407.21787.
- Choi, H. K., Zhu, X. and Li, S. (2025). Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models? NeurIPS 2025. arXiv:2508.17536.
- Dawid, A. P. and Skene, A. M. (1979). Maximum Likelihood Estimation of Observer Error-Rates Using the EM Algorithm. Journal of the Royal Statistical Society, Series C 28(1), 20–28.
- Gao, L., Schulman, J. and Hilton, J. (2023). Scaling Laws for Reward Model Overoptimization. ICML 2023. arXiv:2210.10760.
- Huang, J. et al. (2024). Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. arXiv:2310.01798.
- Jung, J., Brahman, F. and Choi, Y. (2025). Trust or Escalate: LLM Judges with Provable Guarantees for Human Agreement. ICLR 2025. arXiv:2407.18370.
- Kadavath, S. et al. (2022). Language Models (Mostly) Know What They Know. arXiv:2207.05221.
- Kamoi, R. et al. (2024). When Can LLMs Actually Correct Their Own Mistakes? A Critical Survey of Self-Correction of LLMs. TACL. arXiv:2406.01297.
- Khan, A. et al. (2024). Debating with More Persuasive LLMs Leads to More Truthful Answers. ICML 2024. arXiv:2402.06782.
- Kim, E. M., Garg, A., Peng, K. and Garg, N. (2025). Correlated Errors in Large Language Models. ICML 2025. arXiv:2506.07962.
- Kirchner, J. H. et al. (2024). Prover-Verifier Games Improve Legibility of LLM Outputs. arXiv:2407.13692.
- Knight, J. C. and Leveson, N. G. (1986). An Experimental Evaluation of the Assumption of Independence in Multiversion Programming. IEEE Transactions on Software Engineering SE-12(1), 96–109.
- Lightman, H. et al. (2024). Let's Verify Step by Step. ICLR 2024. arXiv:2305.20050.
- Mohri, C. and Hashimoto, T. (2024). Language Models with Conformal Factuality Guarantees. ICML 2024. arXiv:2402.10978.
- Panickssery, A., Bowman, S. R. and Feng, S. (2024). LLM Evaluators Recognize and Favor Their Own Generations. NeurIPS 2024. arXiv:2404.13076.
- Ratner, A. et al. (2016). Data Programming: Creating Large Training Sets, Quickly. NeurIPS 2016; and Ratner, A. et al. (2017). Snorkel: Rapid Training Data Creation with Weak Supervision. VLDB 2017.
- Saad-Falcon, J. et al. (2025). Shrinking the Generation-Verification Gap with Weak Verifiers. NeurIPS 2025. arXiv:2506.18203.
- Song, Y. et al. (2025). Mind the Gap: Examining the Self-Improvement Capabilities of Large Language Models. ICLR 2025. arXiv:2412.02674.
- Stechly, K., Valmeekam, K. and Kambhampati, S. (2025). On the Self-Verification Limitations of Large Language Models on Reasoning and Planning Tasks. ICLR 2025. arXiv:2402.08115.
- Stroebl, B., Kapoor, S. and Narayanan, A. (2024). Inference Scaling fLaws: The Limits of LLM Resampling with Imperfect Verifiers. arXiv:2411.17501.
- Tang, L., Laban, P. and Durrett, G. (2024). MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents. EMNLP 2024. arXiv:2404.10774.
- Verga, P. et al. (2024). Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models. arXiv:2404.18796.
- Wei, J. et al. (2024). Long-form Factuality in Large Language Models. NeurIPS 2024. arXiv:2403.18802.
- Zheng, L. et al. (2023). Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena. NeurIPS 2023. arXiv:2306.05685.
