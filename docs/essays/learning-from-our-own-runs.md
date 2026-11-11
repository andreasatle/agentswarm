# Learning from our own runs: how an agent system can improve from its own history

*A literature study written by Claude (Anthropic) on 11 November 2026 for the AgentSwarm project, from papers read on the web; the agent swarm did not write it. Where the report argues rather than reports, it says so.*

## The question, and the short answer

Every run of an agent system leaves a record: which model attempted which step, what it produced, which checks passed or failed, what it cost, and what was escalated or redone. The question is what the evidence says about turning that record into a better system, without a person deciding each change: which models to call, what to tell them, what to remember, and eventually the models and the system design themselves.

The short answer:

1. **It works, and the cheapest forms work best per dollar.** Learning *which model to call* from logged outcomes cuts cost by half or more at nearly the same quality (Ong et al., 2025; Chen et al., 2023). Learning *what to tell the models*, by having a model read failed runs and rewrite the prompts, beat reinforcement learning on five of six tasks with up to 35 times fewer runs (Agrawal et al., 2025). Learning *what to remember*, as reusable workflows and lessons distilled from past runs, raised success on web tasks by 24–51% relative (Wang et al., 2025) and 9–11% on agent benchmarks (Zhang et al., 2026).
2. **It needs verified signals.** Every method learns from outcomes; if the outcomes are judged wrongly, the system learns the wrong thing faster. The quality of the checks (the subject of the companion essay on verification) is the ceiling on learning, too.
3. **It needs held-out evaluation.** Each method can overfit to the tasks it learned from, and the strongest self-improving systems have been caught gaming their own evaluations. A change is accepted only if it improves results on tasks it did not learn from.
4. **Fine-tuning small models on their own verified successes works** (Zelikman et al., 2022; Chen et al., 2023), and is the natural end point for a system that wants small models to take over steps; but training on unfiltered model output degrades models (Shumailov et al., 2024), so only verified outputs may be used.
5. **Learning the system design itself is possible but expensive.** An agent that rewrote its own code raised its SWE-bench score from 20% to 50%, at a reported cost of about $22,000 for one run (Zhang et al., 2026).

## 1. What a run leaves behind

*This section organises the rest of the report; it is the report's framing, not a finding.*

Not all records are equally useful. Ordered by how much they can be trusted as a learning signal:

| Record | Example | Trust as a signal |
|---|---|---|
| Outcome of a sound check | Tests passed or failed; held-out score | High, up to the check's false-positive rate |
| Outcome of a grounded check | Claim supported or not by its source | Good, if the checker is calibrated |
| Cost and latency | Dollars and seconds per step, per model | Exact |
| Escalations and retries | Step needed three attempts and a larger model | Exact, and informative about difficulty |
| Model judgements | A referee's or reviewer's verdict | Moderate; biased and correlated |
| The model's own judgement | "I think this worked" | Low (Huang et al., 2024; Stechly et al., 2025) |
| Full transcripts | Every prompt and reply | Rich but noisy; useful as raw material for reflection |

A system that wants to learn from its runs should keep the first four as typed data, attached to the step, the model and the prompt version that produced them.

## 2. Learning which model to call

The most mature form of learning from logs is routing. RouteLLM (Ong et al., 2025, ICLR) trains a router on preference data to send each query to a strong or a weak model, and cut cost by more than half with little loss of quality; the routers kept working when the two models were replaced. FrugalGPT (Chen, Zaharia and Zou, 2023) learns a cascade from labelled outcomes, trying cheap models first and escalating on doubt, and matched GPT-4 at up to 98% lower cost on its benchmarks. A line of 2025 work treats routing as a contextual bandit: each call is a trial whose reward is quality minus a cost weight, and the router balances exploring models it is unsure of against exploiting the best one, learning online from the outcomes it observes rather than from a fixed training set (for example "Learning to Route LLMs from Bandit Feedback", 2025).

Routing has three properties that make it the right first thing to learn: its signal (did the step pass its check, and what did it cost) is cheap and usually exact; its decisions are few and easy to audit; and a bad routing decision costs one retry, not a corrupted product.

## 3. Learning what to tell the models

**Prompt optimisation from a metric.** DSPy (Khattab et al., 2024, ICLR) separates a pipeline's structure from its prompts and treats the prompts and few-shot examples as parameters to be optimised against a metric; its optimisers bootstrap examples from the pipeline's own successful runs. With a few lines of code and minutes of compiling, GPT-3.5 and a 13B open model outperformed standard few-shot prompting, and small models compiled this way were competitive with expert-written prompt chains on GPT-3.5. Earlier, OPRO (Yang et al., 2024) showed a model can act as the optimiser itself, proposing new prompts from a history of past prompts and their scores.

**Reflecting on failures.** GEPA (Agrawal et al., 2025) has a model read the *traces* of runs (reasoning, tool calls, tool outputs, and the checker's feedback), diagnose in words what went wrong, and propose prompt changes, which are tested and kept on a Pareto front of candidates so that one task's improvement does not erase another's. Against GRPO, a reinforcement-learning method using 24,000 runs, GEPA did better on five of six tasks, by up to 20%, using between 4 and 35 times fewer runs; on IFBench it beat GRPO after 678 runs. The lesson is that a natural-language trace with a check's verdict is a far richer signal than a scalar reward, and a model can use it directly.

**Lesson:** prompts are cheap to change, cheap to test and easy to roll back, and runs that failed a check are the best material to improve them from.

## 4. Learning what to remember

A second family keeps the model and prompt fixed and changes what the agent is given to read: a memory distilled from past runs.

- **Reflections.** Reflexion (Shinn et al., 2023) has an agent write a short verbal reflection after a failure and read it on the next attempt at the same task.
- **Insights across tasks.** ExpeL (Zhao et al., 2024, AAAI) collects successful and failed trajectories on training tasks, extracts natural-language insights by comparing them, and gives the insights and similar past trajectories to the agent on new tasks; insights learned on one task set transferred to another (FEVER, 70% against 63% for the baseline).
- **Workflows.** Agent Workflow Memory (Wang, Mao, Fried and Neubig, 2025, ICML) induces reusable workflows, common sub-routines with their steps, from past successes and offers them on new tasks; it improved success by 24.6% (Mind2Web, relative) and 51.1% (WebArena, relative), beat a version with human-written workflows, and reduced the steps taken.
- **Skills as code.** Voyager (Wang et al., 2023) stores each skill it learns in Minecraft as executable code in a library it can call and compose; the library carried over to new worlds.
- **Lessons from failures as well as successes.** ReasoningBank (Ouyang et al., 2026, ICLR) distils compact strategy items from both successful and failed attempts, judged by the agent; it beat memories of raw trajectories or of successes only, for example +8.3% on WebArena with Gemini 2.5 Flash.
- **Playbooks that grow without collapsing.** Agentic Context Engineering (Zhang et al., 2026, ICLR) keeps an evolving playbook of strategies, updated by generation, reflection and curation. It names two failure modes of learned prompts: *brevity bias*, where optimisation drifts to short, generic instructions, and *context collapse*, where repeatedly rewriting the whole context erodes it into a vague summary and performance drops sharply. Its remedy is incremental, itemised updates that add and amend entries rather than rewrite. It gained 10.6% on agent tasks and 8.6% on finance tasks, and on the AppWorld leaderboard matched the top production agent using a smaller open model.

**Lesson:** store lessons as structured, itemised, versioned entries tied to the kind of step they apply to; include what failed and why; add and amend rather than rewrite; and retrieve only the entries relevant to the step at hand.

## 5. Learning the models themselves

**Training on one's own verified successes.** STaR (Zelikman et al., 2022, NeurIPS) has a model generate reasoning for many questions, keep only the reasoning that reached the correct answer, fine-tune on it, and repeat; for questions it gets wrong, it generates reasoning given the answer ("rationalisation") so the hard cases also contribute. On CommonsenseQA it matched a model 30 times larger. FireAct (Chen et al., 2023) fine-tuned Llama-2-7B on 500 agent trajectories generated by GPT-4 and raised its HotpotQA score by 77% (from 14.8 to 26.2), and found more diverse trajectories helped further. This is the route by which a small model takes over a step a large model used to do: collect the large model's verified successes on that step, then fine-tune the small one.

**The filter is essential.** Shumailov et al. (2024, Nature) showed that models trained recursively on model-generated data degrade: the tails of the original distribution disappear and errors compound over generations. Training on model output is safe only when the output has been checked, which is exactly what STaR's correctness filter and FireAct's successful-trajectory filter do.

**Lesson:** fine-tuning is the most expensive and least reversible form of learning, and the one with the largest long-run payoff for cost; it should come last, on a step whose verified successes have piled up.

## 6. Learning the system design

At the far end, the system rewrites itself. Automated Design of Agentic Systems (Hu, Lu and Clune, 2025, ICLR) has a meta agent program new agent designs, test them and keep an archive of the best. The Darwin Gödel Machine (Zhang et al., 2026, ICLR) keeps an archive of coding agents that modify their own code; selected agents propose changes to themselves, which are kept if they score well. It raised its SWE-bench score from 20.0% to 50.0% and Polyglot from 14.2% to 30.7%, and beat ablations without self-modification or without open-ended exploration. Press coverage reported a single run costing about $22,000 over two weeks, and cases where the agent gamed its own evaluation. These results show the ceiling is high, and that open-ended self-modification needs sandboxing, held-out evaluation and a large budget.

## 7. What makes learning from runs work

*Drawn from the evidence above; the ordering is this report's.*

1. **Verified signals.** All methods above learn from outcomes. Outcomes judged by weak or biased checks teach the system to please the checks (Gao et al., 2023, on reward over-optimisation; the companion verification essay). Learn from sound and grounded checks; treat model judgements as weaker evidence; never learn from self-judgement alone.
2. **Held-out evaluation and a ratchet.** Every learned change, a prompt, a memory entry, a routing rule, a fine-tuned model, is evaluated on tasks it did not learn from and accepted only if it improves them. This is the "stochastic descent" loop: sample a change, keep it only if the held-out loss falls.
3. **Failures are the richest data.** GEPA, ExpeL and ReasoningBank all gain most from contrasting failed and successful attempts; a system that only keeps successes throws away the information about what to avoid.
4. **Small, reversible changes first.** Routing, then prompts and memory, then fine-tuning, then design: in that order the changes become more expensive, less reversible and harder to audit, and the evidence for them is also in that order.
5. **Guard against collapse and drift.** Incremental, itemised memory (ACE); filtered training data (Shumailov et al.); and periodic checks that what was learned still holds as models and tasks change.

## 8. Learning at each scale

*This section connects to the multiscale reading of the companion essay* (Can many small models compete with one large model?).

In the heterogeneous multiscale method, the macro solver, the micro solvers and the operators that pass data between them are each designed separately. Learning from runs splits the same way:

| Level | What is learned | From which records | Methods |
|---|---|---|---|
| Micro (small models doing steps) | Which model for which kind of step; its prompt; eventually its weights | Step outcomes, retries, costs | Routing and bandits; GEPA and DSPy; STaR and FireAct |
| Macro (planning and decomposition) | Decomposition templates and workflows that worked; where to split further | Plans, transfer failures, replans | Agent Workflow Memory; ADaPT-style refinement statistics |
| Between levels (checks and context passing) | Checker accuracy and thresholds; what context a step needs | Verdicts, disagreements, integration failures | Label-free accuracy estimation and calibration (verification essay) |

The micro level has by far the most data, since every step leaves a record, so it is where learning starts. The macro level learns slowly, from few plans, and benefits most from memory of whole workflows. The checks learn from disagreements and from a small labelled set, and improving them raises the ceiling for everything else.

## 9. What this means for AgentSwarm (briefly)

AgentSwarm already keeps much of the right record: an event log of every claim, attempt, check, escalation and cost, held-out tests for software, and a router that samples models and learns their success rates. The literature suggests an order for the rest: first, make sure every outcome on the board is tied to the model, the prompt version and the check that judged it, so the log is a clean dataset; next, routing from that data, which already exists in part; then prompt improvement from failed steps in the style of GEPA, with each change accepted only on held-out tasks; then an itemised memory of lessons per kind of step, in the style of ACE and Agent Workflow Memory; and only later, once a kind of step has many verified successes from a large model, fine-tuning a small model on them.

## 10. Open questions

- **How much data does each kind of learning need?** Routing needs tens of outcomes per model and kind of step; prompt optimisation hundreds of runs; fine-tuning hundreds to thousands of verified examples. For a system working on one real problem at a time, data is scarce, and which learning pays first depends on how fast it accumulates.
- **Does what is learned transfer between problems?** ExpeL's insights transferred between related task sets; whether lessons from building one parser help build another, or help write a document, is untested.
- **How to keep learning honest over time?** Held-out tasks get used up; checks get gamed; models get replaced. Refreshing the held-out set and re-validating learned changes after each model upgrade is necessary, and costs runs.

## References

- Agrawal, L. A. et al. (2025). GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning. arXiv:2507.19457.
- Chen, B. et al. (2023). FireAct: Toward Language Agent Fine-tuning. arXiv:2310.05915.
- Chen, L., Zaharia, M. and Zou, J. (2023). FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance. arXiv:2305.05176.
- Gao, L., Schulman, J. and Hilton, J. (2023). Scaling Laws for Reward Model Overoptimization. ICML 2023. arXiv:2210.10760.
- Hu, S., Lu, C. and Clune, J. (2025). Automated Design of Agentic Systems. ICLR 2025. arXiv:2408.08435.
- Huang, J. et al. (2024). Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. arXiv:2310.01798.
- Khattab, O. et al. (2024). DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines. ICLR 2024. arXiv:2310.03714.
- Ong, I. et al. (2025). RouteLLM: Learning to Route LLMs with Preference Data. ICLR 2025. arXiv:2406.18665.
- Ouyang, S. et al. (2026). ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory. ICLR 2026. arXiv:2509.25140.
- Shinn, N. et al. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. NeurIPS 2023. arXiv:2303.11366.
- Shumailov, I. et al. (2024). AI models collapse when trained on recursively generated data. Nature 631, 755–759.
- Stechly, K., Valmeekam, K. and Kambhampati, S. (2025). On the Self-Verification Limitations of Large Language Models on Reasoning and Planning Tasks. ICLR 2025. arXiv:2402.08115.
- Wang, G. et al. (2023). Voyager: An Open-Ended Embodied Agent with Large Language Models. arXiv:2305.16291.
- Wang, Z. Z., Mao, J., Fried, D. and Neubig, G. (2025). Agent Workflow Memory. ICML 2025. arXiv:2409.07429.
- Yang, C. et al. (2024). Large Language Models as Optimizers. ICLR 2024. arXiv:2309.03409.
- Learning to Route LLMs from Bandit Feedback: One Policy, Many Trade-offs (2025). arXiv:2510.07429.
- Zelikman, E., Wu, Y., Mu, J. and Goodman, N. (2022). STaR: Bootstrapping Reasoning With Reasoning. NeurIPS 2022. arXiv:2203.14465.
- Zhang, J. et al. (2026). Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents. ICLR 2026. arXiv:2505.22954.
- Zhang, Q. et al. (2026). Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models. ICLR 2026. arXiv:2510.04618.
- Zhao, A. et al. (2024). ExpeL: LLM Agents Are Experiential Learners. AAAI 2024. arXiv:2308.10144.
