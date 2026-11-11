# Can many small models compete with one large model? What the evidence says, and agents as a multiscale problem

*A literature study, 10 November 2026. Written by Claude for Andreas Atle, from papers and engineering reports read on the web; no AgentSwarm runs were used. Where the report argues rather than reports, it says so.*

## The question, and the short answer

The question is whether a system of many small or cheap language models, organised as agents, can match a single large model on real problems, and at lower cost. A second question is whether it helps to see such a system as a *multiscale* problem, in the sense of numerical analysis: a coarse level that sees the whole and fine levels that do local work, coupled so that each level supplies what the other lacks.

The short answer from the literature:

1. **Yes, under conditions that are now fairly well understood.** Small models with many attempts and a reliable check beat large models with one attempt, at a fraction of the cost (Brown et al. 2024; Stroebl et al. 2024). An 8B model that orchestrates larger models and tools beat GPT-5 alone on Humanity's Last Exam at about 30% of the cost (Su et al. 2025). Small models on a decomposed task executed a million dependent steps without an error, which no single model could (Meyerson et al. 2025).
2. **No, by default.** Most multi-agent systems gain little over a single agent (Cemri et al. 2025). Under matched budgets, multi-agent setups lose 39–70% on tasks with sequential dependencies, and once a single agent already scores above about 45%, adding agents tends to hurt (Kim et al. 2025). Debate adds little beyond majority voting (Choi et al. 2025), mixing different models is often worse than sampling the best one repeatedly (Li et al. 2025), and voting itself can get *worse* with more calls on hard questions (Chen et al. 2024).
3. **The conditions that separate the two are exactly the ones a multiscale method needs:** the task must decompose into pieces that are weakly coupled at the fine scale (scale separation); each piece must be small enough that a small model's error rate on it is low; errors must be caught locally by something more reliable than the generator (a verifier, a test, or a vote over independent attempts); and the pieces must not share so much context that passing it between agents costs more than it saves.

The rest of the report gives the evidence for each of these, then argues the multiscale reading, then proposes how to test the claim properly.

## 1. Repeated attempts: the cheapest form of "many models"

The simplest swarm is one model asked many times, with a rule for choosing among the answers.

**Voting helps, then saturates or reverses.** Self-consistency (Wang et al., 2022) samples several reasoning chains and takes the majority answer. Li et al. (2024, "More Agents Is All You Need") scaled this to ensembles of 15–20 and found that Llama2-70B and GPT-3.5-Turbo then reach the accuracy of their stronger counterparts on their tasks. But Chen et al. (2024, "Are More LLM Calls All You Need?") showed that voting accuracy can rise and then *fall* as calls increase. The reason is a mixture of query difficulties: on a query a model gets right more than half the time, voting drives accuracy towards 1; on a query it gets right less than half the time, voting drives it towards 0. A task with both kinds has an optimal number of calls, which they predict from a small sample. This is the Condorcet jury theorem with its precondition made visible: voting helps only where each voter is better than chance and the voters' errors are independent.

**Errors are not independent.** Kim et al. (2025, "Correlated Errors in Large Language Models", ICML) evaluated over 350 models and found that when two models are both wrong, they give the *same* wrong answer about 60% of the time on one leaderboard. Errors are more correlated between models from the same provider, with shared architectures, and between more accurate models. Repeated samples from one model are more correlated still. Voting over correlated voters gains much less than the independent-error theory promises, and this is the most important single number for anyone hoping that many weak voters add up to a strong one.

**With a verifier, coverage is the right measure.** Brown et al. (2024, "Large Language Monkeys") measured *coverage*, the fraction of problems solved by at least one of k attempts, and found it grows roughly log-linearly with k over several orders of magnitude. On SWE-bench Lite, DeepSeek-Coder-V2 went from 15.9% with one attempt to 56% with 250, above the 43% single-attempt state of the art of the time; five attempts from the cheaper model solved more issues than one from GPT-4o or Claude 3.5 Sonnet, at over 3x lower cost. The catch is selection: where unit tests can pick the right attempt, coverage becomes accuracy; where they cannot, majority voting and reward models plateau after a few hundred samples.

**An imperfect verifier caps the gain.** Stroebl, Kapoor and Narayanan (2024) showed that when the verifier has false positives (tests that pass wrong code), resampling cannot push the error below the false-positive rate, whatever the budget. Weaker models produce more false positives, so their ceiling is lower, and the optimal number of samples is often under ten. A weak model cannot be resampled up to a strong model's accuracy unless the verifier is strong. Saad-Falcon et al. (2025, "Weaver") narrowed the gap by combining many weak verifiers with weak supervision: Llama 3.3 70B reached o3-mini-level accuracy on reasoning benchmarks, and a 400M distilled verifier kept about 98% of that at a tiny fraction of the verification compute. Verification, not generation, is where the leverage is.

**Lesson:** "many small models" is first of all *many attempts plus a good selector*. Its value is governed by three numbers: the per-attempt success rate, the correlation between attempts, and the false-positive rate of the selector.

## 2. Mixing different models

If one model's samples are correlated, different models might be less so.

Mixture-of-Agents (Wang et al., 2024; ICLR 2025) layers several models: each layer's models see all the previous layer's answers and write better ones, and an aggregator writes the final answer. Using only open-source models it reached a 65.1% length-controlled win rate on AlpacaEval 2.0 against 57.5% for GPT-4o, the headline evidence that a group of weaker models can beat a frontier model.

Li, Lin, Xia and Jin (2025, "Rethinking Mixture-of-Agents", TMLR 2026) then showed that **Self-MoA**, the same architecture fed only with samples from the single best model, beat the mixed version by 6.6% on AlpacaEval 2.0 and 3.8% on average across MMLU, CRUX and MATH. Mixing in weaker models lowers the average quality of the inputs more than their diversity helps; mixing won only in a task built for it, by 0.2–0.35%. Diversity is worth something only when the extra models are nearly as good.

Debate is the other classic mixing scheme (Du et al., 2023). Choi, Zhu and Li (2025, "Debate or Vote", NeurIPS) found across seven benchmarks that majority voting alone accounts for most of the gains attributed to multi-agent debate, and proved that debate without an external signal is a martingale over the agents' beliefs: it does not improve expected correctness. Agents talking to each other do not create information; checks against the world do.

**Lesson:** heterogeneity is not free diversity. Pick the strongest affordable model for each role and get independence from the task structure and from verification, not from weaker voices.

## 3. Division of labour: roles, routing and orchestration

The second family splits the *work*, not just the attempts.

**Routing and cascades.** FrugalGPT (Chen, Zaharia and Zou, 2023) tries a cheap model first and escalates when a learned scorer doubts the answer; on its benchmarks it matched GPT-4 at up to 98% lower cost, or beat it by 4% at equal cost. RouteLLM (Ong et al., 2024; ICLR 2025) trains a router on human preference data to send each query to a strong or a weak model, cutting cost by more than half with little loss of quality, and the routers kept working when the two models were swapped. These are the most mature and best-replicated results in the field: most queries do not need the big model, and a learned decision can find which ones do.

**Specialised small models.** Shen et al. (2024, "Small LLMs Are Weak Tool Learners", EMNLP) split a tool-using agent into a planner, a caller and a summarizer, each a fine-tuned 7B model; together they beat a single 13B model doing all three. Belcak et al. (2025, NVIDIA) argue in a position paper that most calls inside agentic systems are narrow and repetitive and that models under about 10B parameters are sufficient, more suitable and far cheaper for them; it is an argument, not a measurement.

**A small model as the conductor.** ToolOrchestra (Su et al., 2025, NVIDIA and HKU) trained an 8B orchestrator with reinforcement learning to decide, step by step, which tool or model to call, including frontier models, with rewards for correctness, cost and user preference. It scored 37.1% on Humanity's Last Exam against 35.1% for GPT-5 alone, at about 30% of the cost, calling GPT-5 only one or two times per question. This is the strongest recent evidence for the claim in its useful form: not that small models replace the large one, but that a small model deciding *when* the large one is needed beats the large one used for everything.

**Large teams.** Anthropic's research system (2025) uses a lead agent (Claude Opus 4) that spawns parallel subagents (Claude Sonnet 4); it beat single-agent Opus 4 by 90.2% on an internal research evaluation. On BrowseComp, three factors explained 95% of the variance in performance, and token usage alone explained 80%; multi-agent runs used about 15 times the tokens of a chat. Anthropic says the gain comes from spending more tokens in parallel on independent directions, that tasks where agents must share context or depend on each other are a poor fit, and that most coding has fewer parallel parts than research. Cognition (2025, "Don't Build Multi-Agents") argues from the same experience for single-threaded agents with full shared context, and in 2026 allowed a narrower pattern: several agents may contribute thinking, but writes stay in one thread. Qian et al. (2024, MacNet, ICLR 2025) scaled collaboration to over a thousand agents on a graph and found quality grows *logistically* in the number of agents: it saturates.

**Lesson:** much of the measured multi-agent gain is more compute. The honest comparison holds compute equal (Kapoor et al., 2024, "AI Agents That Matter", found that with cost controlled, simple baselines such as retrying matched or beat elaborate agent architectures on HumanEval).

## 4. When multi-agent systems fail, and why

Cemri et al. (2025, "Why Do Multi-Agent LLM Systems Fail?", NeurIPS) annotated over 1,600 traces from seven popular frameworks and found 14 failure modes in three groups: system design (poor role and task specification, steps repeated, termination unclear), inter-agent misalignment (information withheld or ignored, derailing, mismatch between reasoning and action) and task verification (no check, a wrong check, or stopping early). They note that gains over single agents on popular benchmarks are often minimal, and that the failures come mostly from design, not from the models.

Kim et al. (2025, "Towards a Science of Scaling Agent Systems", Google Research and MIT) is the most controlled study so far: five architectures (single agent, and independent, centralized, decentralized and hybrid multi-agent), three model families, six agentic benchmarks and 260 configurations with matched prompts, tools and reasoning budgets. Their findings:

- On a parallelisable task (financial analysis) centralized coordination gained 80.8%; on a strictly sequential planning task every multi-agent variant lost 39–70%; on SWE-bench Verified every multi-agent variant was slightly worse.
- Once the single agent already scored above about 45%, adding agents gave negative returns (a significant negative coefficient). Coordination helps weak baselines on decomposable work, not strong ones.
- Independent agents with no checking amplified errors 17.2-fold at the trace level; a central coordinator that checks cut this to 4.4-fold.
- Tool-heavy tasks suffered most from coordination overhead under a fixed budget.
- A model from measured task properties predicted the best architecture for 87% of held-out configurations. Their summary: architecture-task alignment, not the number of agents, decides.

**Lesson:** the structure of the task decides, and it can be measured before choosing an architecture.

## 5. Long tasks: where small models with structure win clearly

Sinha et al. (2025, "The Illusion of Diminishing Returns", ICLR 2026) separated *execution* from reasoning by giving models the plan and the knowledge and only asking them to carry it out. If each step succeeds with probability p, a task of s dependent steps succeeds with probability about p^s, so the longest task a model can complete grows sharply as p approaches 1: small gains in per-step accuracy compound into much longer tasks. They also found *self-conditioning*: models make more mistakes when their context already contains their own earlier mistakes, and scaling the model does not remove it.

Meyerson et al. (2025, "Solving a Million-Step LLM Task with Zero Errors", Cognizant and UT Austin) turned this into a construction, MAKER: decompose the task maximally, so each microagent makes one decision with only the context it needs; vote on each step until one answer leads by k (first-to-ahead-by-k); and discard answers that show signs of confusion (too long, malformed) before voting. With gpt-4.1-mini, a small non-reasoning model whose per-step error was about 0.2%, k = 3 was enough to execute the 1,048,575 dependent moves of a 20-disk Tower of Hanoi without a single error. Their analysis gives an expected cost of order s ln s for s steps under maximal decomposition: the votes needed per step grow only logarithmically with the task length, while the cost grows exponentially if each agent takes several steps at once. They found small non-reasoning models the most cost-effective. The limits are stated plainly: the decomposition and the algorithm were given, the task has a known solution, only one task was tested, and the theory assumes independent errors between steps.

**Lesson:** for long tasks, the decisive quantity is the per-step error rate after local error correction, and structure (small steps, fresh context, local voting) beats model size there.

## 6. Agents as a multiscale problem

*This section is an argument, built on the evidence above.*

In numerical analysis a problem is multiscale when its behaviour at a coarse scale depends on processes at a fine scale that are too expensive to resolve everywhere. The heterogeneous multiscale method (E and Engquist, 2003) gives the template: a *macro solver* advances the coarse variables using a model with missing data, and *micro solvers*, run locally on small domains, supply that data. It works when the scales separate, so a micro problem can be solved on a small domain without knowing the rest; the error analysis then splits into the macro error, the micro error and the error of passing data between them. Multigrid adds the other half: fine-level smoothing removes local errors cheaply, and a coarse-level correction removes the global errors the fine level cannot see.

Read an agent system the same way.

| Multiscale method | Agent system | Evidence |
|---|---|---|
| Macro solver: global structure, coarse variables | Planner, orchestrator or lead agent; the plan, the interfaces, the definition of done | ToolOrchestra, Anthropic's lead agent, ADaPT |
| Micro solver: local, cheap, run many times | Small models doing one well-specified step with minimal context | MAKER microagents, α-UMi roles, routing to cheap models |
| Scale separation | Steps that can be done without knowing the rest; little shared context | Kim et al.: parallelisable +81%, sequential −39 to −70%; Cognition and Anthropic on shared context |
| Local error control (smoothing) | Voting, verification, tests on each step | MAKER's first-to-ahead-by-k; Monkeys and Weaver; the 17.2x vs 4.4x amplification |
| Coarse correction | Replanning when local work fails; escalating to a larger model | ADaPT's as-needed decomposition; FrugalGPT and RouteLLM cascades |
| Adaptive refinement | Decompose further only where the executor fails | ADaPT: up to +28% on ALFWorld, +27% WebShop, +33% TextCraft |
| Error estimate per level | Per-step error rate p, error correlation ρ, verifier false-positive rate | Sinha et al., Kim (correlated errors), Stroebl et al. |

The multiscale reading predicts the results above rather than merely describing them:

- **Where scales separate, fine levels can be cheap.** A small model's error on a narrow step is low, and local voting drives it lower at logarithmic cost, so the total cost of the fine level grows like s ln s rather than exponentially (MAKER). This is the regime in which many small models beat one large one.
- **Where scales do not separate, the method breaks down.** On a sequential planning task every step depends on the global state; there is no small domain to solve locally, and splitting the work only adds the cost of passing context, which is the data-transfer error between levels (Kim's −39 to −70%, Cognition's argument). Here a single strong model with the whole context is the right solver.
- **The coarse solver must be good, but it is called rarely.** In HMM the macro solver advances on a coarse grid; in an agent system the expensive model plans and decides, and is called once or twice per task (ToolOrchestra's 1–2 GPT-5 calls). The cost is dominated by the fine level, which is where small models pay off.
- **Local error control is what makes the fine level trustworthy.** Without it errors compound across steps (Sinha's p^s; Kim's 17-fold amplification). With independent checks they do not. Correlated errors are the analogue of a smoother that fails on certain error modes: votes among similar models cannot remove errors they all share (60% agreement when wrong), so those must be caught by a different kind of check, a test or a verifier of another family, exactly as multigrid needs the coarse correction for the modes smoothing cannot reach.
- **Refine adaptively.** ADaPT decomposes only where the executor fails, which is adaptive mesh refinement: spend fine-scale work only where the solution needs it.
- **There is an optimal level of resolution.** Too coarse and each agent's steps are too long for its error rate; too fine and the coordination and context-passing overhead dominate (MacNet's logistic saturation, Kim's 200–300% overhead). In numerical terms, there is a cost-optimal grid, and it depends on the error rate of the micro solver.

What the analogy does *not* give for free is the decomposition itself. In HMM the scales are known from the physics; in MAKER the decomposition was supplied by hand. Finding a decomposition with good scale separation is the hard, largely open problem, and the evidence suggests it is the job of the strongest model in the system.

## 7. What this means for AgentSwarm (briefly)

AgentSwarm's design already follows several of these lessons without having measured them: work decomposed into small steps on a shared board, independent acceptance checks the producer cannot change, held-out tests to measure generalisation, cheap builders with escalation to strong models, and a referee of another model family. Its results so far (for example CommonMark at 100% visible and held out, jq hardened from 87% to 94% held out) show the framework finishing real problems; they do not yet show competitiveness, because no matched comparison against a single large model has been run. This report is not based on those runs.

## 8. How to test the claim properly

The literature points to a design for the competitiveness study (goal 2 in COLLABORATION.md), and to the measurements that would make it worth reading:

1. **Compare at equal cost, and plot the frontier.** For each problem, run the single large model and the swarm at several budgets and plot accuracy on held-out checks against dollars. A swarm "competes" if its curve lies on or above the single model's over some range. A single number at one budget hides the answer (Kapoor et al.; Kim et al.).
2. **Include the strong simple baselines.** The large model alone; the large model with retries against the same tests; the small model with retries (Monkeys); the small model routed or cascaded to the large one (FrugalGPT, RouteLLM). A swarm that does not beat "retry with tests" has not shown anything about collaboration.
3. **Measure the multiscale quantities.** Per-step error rate p for each model on each kind of step; the correlation of errors between attempts and between models; the false-positive rate of the checks; the fraction of steps that needed escalation; and the overhead of coordination in tokens. These predict where the swarm should win, which turns the study from a contest into a test of a theory.
4. **Vary task structure on purpose.** Problems that are parallelisable (independent modules, independent sections), sequential (one long chain of dependent decisions) and mixed. The prediction is clear: the swarm should win on the first, lose on the second, and its advantage should grow with problem size on the first (MAKER's s ln s against a single model's p^s).
5. **Grow the problem.** The ROADMAP's plan of the same problems at increasing size is the right one; the literature's most interesting effects (compounding errors, logarithmic voting cost, the 45% ceiling) appear only as tasks get longer.
6. **Watch the known traps.** Correlated errors among same-provider models; imperfect verifiers that cap accuracy; self-conditioning on earlier mistakes (use fresh context per step); and coordination overhead on tool-heavy tasks.

## 9. Open questions

- **Who decomposes?** All the clear wins used a decomposition given by people (MAKER) or learned on a narrow distribution (ToolOrchestra). Whether a model can find decompositions with good scale separation for new problems is not established.
- **How independent can checks be made?** Most of the cost-effectiveness rests on verifiers that are cheaper and more reliable than generators; outside code and maths, such verifiers are rare. Weak-verifier ensembles (Weaver) are the best lead.
- **Does it hold at scale and over time?** Most studies are on benchmarks of hundreds of items, with models that are replaced within a year. The finding that a better model is worth more than twice the tokens (Anthropic) means the frontier keeps moving, and a swarm's advantage has to be re-measured with each generation.

## References

- Anthropic (2025). How we built our multi-agent research system. https://www.anthropic.com/engineering/multi-agent-research-system
- Belcak, P. et al. (2025). Small Language Models are the Future of Agentic AI. arXiv:2506.02153.
- Brown, B. et al. (2024). Large Language Monkeys: Scaling Inference Compute with Repeated Sampling. arXiv:2407.21787.
- Cemri, M. et al. (2025). Why Do Multi-Agent LLM Systems Fail? NeurIPS 2025. arXiv:2503.13657.
- Chen, L., Zaharia, M. and Zou, J. (2023). FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance. arXiv:2305.05176.
- Chen, L. et al. (2024). Are More LLM Calls All You Need? Towards the Scaling Properties of Compound AI Systems. NeurIPS 2024. arXiv:2403.02419.
- Choi, H. K., Zhu, X. and Li, S. (2025). Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models? NeurIPS 2025. arXiv:2508.17536.
- Cognition (Yan, W.) (2025). Don't Build Multi-Agents. https://cognition.ai/blog/dont-build-multi-agents ; and (2026) Multi-Agents: What's Actually Working. https://cognition.ai/blog/multi-agents-working
- Du, Y. et al. (2023). Improving Factuality and Reasoning in Language Models through Multiagent Debate. arXiv:2305.14325.
- E, W. and Engquist, B. (2003). The heterogeneous multiscale methods. Communications in Mathematical Sciences 1(1), 87–132.
- Kapoor, S. et al. (2024). AI Agents That Matter. arXiv:2407.01502.
- Kim, E. M., Garg, A., Peng, K. and Garg, N. (2025). Correlated Errors in Large Language Models. ICML 2025. arXiv:2506.07962.
- Kim, Y. et al. (2025). Towards a Science of Scaling Agent Systems. arXiv:2512.08296.
- Li, J. et al. (2024). More Agents Is All You Need. TMLR 2024. arXiv:2402.05120.
- Li, W., Lin, Y., Xia, M. and Jin, C. (2025). Rethinking Mixture-of-Agents: Is Mixing Different Large Language Models Beneficial? TMLR 2026. arXiv:2502.00674.
- Meyerson, E. et al. (2025). Solving a Million-Step LLM Task with Zero Errors. arXiv:2511.09030.
- Ong, I. et al. (2024). RouteLLM: Learning to Route LLMs with Preference Data. ICLR 2025. arXiv:2406.18665.
- Prasad, A. et al. (2024). ADaPT: As-Needed Decomposition and Planning with Language Models. Findings of NAACL 2024. arXiv:2311.05772.
- Qian, C. et al. (2024). Scaling Large Language Model-based Multi-Agent Collaboration. ICLR 2025. arXiv:2406.07155.
- Saad-Falcon, J. et al. (2025). Shrinking the Generation-Verification Gap with Weak Verifiers. NeurIPS 2025. arXiv:2506.18203.
- Shen, W. et al. (2024). Small LLMs Are Weak Tool Learners: A Multi-LLM Agent. EMNLP 2024. arXiv:2401.07324.
- Sinha, A., Arun, A., Goel, S., Staab, S. and Geiping, J. (2025). The Illusion of Diminishing Returns: Measuring Long Horizon Execution in LLMs. ICLR 2026. arXiv:2509.09677.
- Stroebl, B., Kapoor, S. and Narayanan, A. (2024). Inference Scaling fLaws: The Limits of LLM Resampling with Imperfect Verifiers. arXiv:2411.17501.
- Su, H. et al. (2025). ToolOrchestra: Elevating Intelligence via Efficient Model and Tool Orchestration. arXiv:2511.21689.
- Wang, J. et al. (2024). Mixture-of-Agents Enhances Large Language Model Capabilities. ICLR 2025. arXiv:2406.04692.
- Wang, X. et al. (2022). Self-Consistency Improves Chain of Thought Reasoning in Language Models. ICLR 2023. arXiv:2203.11171.
