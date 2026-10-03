# Backlog: deliberately deferred work

Everything we decided *not* to do yet, so it isn't forgotten. Each item says where the decision was made
and when it is due. When an item is done, delete it here in the same commit.

| Item | Why deferred | Due | Decided in |
| --- | --- | --- | --- |
| **Jev relevance filters for teams** (§12.1 "Team relevance filter", listed under Phase 2 in §19) | The executor, the only team, decides relevance deterministically from history. A Jev filter pays off once tester/reviewer teams exist. | Phase 3, with the first non-executor team | Phase 2 design, Q3 |
| **Human workflow for escalated artifacts** | Escalated artifacts stay parked; nothing un-parks them. | Phase 3 (supervisor, escalation queue §14.1) | Phase 1 review |
| **Promotion beyond `experimental`** | Level branches mix many artifacts; "promote artifact X to candidate" needs a design first. | Before Phase 3 adds the candidate level | Phase 0 review |
| **`LLMDecider` fallback** (§12.4) | In shadow mode a Jev outage only loses log entries. | Phase 4 (Jev active) | Phase 2 design |
| **Retries for Jev calls** | Same as above: harmless in shadow mode. | Phase 4 | Phase 2 step 1 |
| **Score and Choice answers in gate policies** | The first judge is Noul only; thresholds for the others need calibration data. | Phase 4 | Phase 2 design |
| **Per-item checks and the LLM explainer** (§11.6, sources 3 and 4) | Not needed for the first judge. | Phase 3 or 4 | Phase 2 design |
| **Model-tier cascade** (§13.3) | One model per team is enough until routing is calibrated. | Phase 4 | Phase 1 plan |
| **Async Jev calls** | Gates are synchronous today. | Phase 6 (scheduler) | Phase 2 design, Q5 |
| **Faster test suite** | The suite-validation and mutation tests start ~60 pytest processes; the full run takes ~70 s. Mark them `slow` and run them in CI or on demand if this starts to hurt. | When the full run passes ~2 min | Phase 2 step 7 |
| **`in_scope` truth for model-executor runs** | Only scripted runs have a known `in_scope`; model runs get it from spot checks only. A scope rule (allowed paths per task) could add a weak automatic source. | Phase 3 or 4 | Phase 2 step 6 |
| **Sharpen the judge's question wording** | First real run: Jev lowers p(in_scope) for trivial tests, mixing two questions. §12.2 asks for one narrow property per question. Tune with calibration data. | Phase 4 | Phase 2, first real run |
| **A harder task suite** | In the first full run the model executor solved all 14 greeter tasks, so model runs gave no negative examples. Tasks the executor sometimes fails would. | Phase 4 (calibration) | Phase 2 result |
| **Spend budgets** (`budgets.usd_per_day`, §14.1) | Needs per-call cost tracking from the model layer. | Phase 6 (scheduler) | Phase 3 step 8 |
| **Explicit revert of a merged artifact** (§9) | `resolve --drop` retires an artifact but its merged code stays on the level branches. Reverting it is an explicit, logged action the proposal reserves for a person or the supervisor. | Phase 4 or later | Phase 3 step 9 |
