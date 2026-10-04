# Backlog: deliberately deferred work

Everything we decided *not* to do yet, so it isn't forgotten. Each item says where the decision was made
and when it is due. When an item is done, delete it here in the same commit.

| Item | Why deferred | Due | Decided in |
| --- | --- | --- | --- |
| **Score and Choice answers in gate policies** | Every judge so far is Noul only; thresholds for the others need calibration data. | When a judge needs graded answers | Phase 2 design |
| **Per-item checks and the LLM explainer** (§11.6, sources 3 and 4) | Not needed by the current judges. | When a judge needs them | Phase 2 design |
| **Async Jev calls** | Gates now run in worker threads (Phase 6), so projects overlap; a native async client would only save threads. | If thread count matters | Phase 2 design, Q5 |
| **Faster test suite** | The full run takes ~2 minutes (suite validation and mutation tests start many pytest processes). Mark them `slow` and run them in CI or on demand. | Now due: with CI | Phase 2 step 7 |
| **`in_scope` truth for model-executor runs** | Only scripted runs have a known `in_scope`; model runs get it from spot checks only. A scope rule (allowed paths per task) could add a weak automatic source. | With the next evaluation work | Phase 2 step 6 |
| **Explicit revert of a merged artifact** (§9) | `resolve --drop` retires an artifact but its merged code stays on the level branches. Reverting it is an explicit, logged action the proposal reserves for a person or the supervisor. | When dropping merged work is needed | Phase 3 step 9 |

Done and removed in the 2026-10-04 cleanup (each in its phase's design doc): relevance filters and the
escalation workflow (Phase 3), promotion beyond the base level (release trains, Phase 3), the LLM fallback,
Jev retries, model tiers, sharper wording and the harder suite (Phase 4), spend budgets (Phase 6).

## Open items (updated 2026-10-04)

- **Prove the active gate's false-promotion rate.** eval-3 (in sample) and eval-4 (held out) give 1/41
  false promotions pooled and 0/16 held out. The 95% upper bound gets to 5% only at about 73 known
  promotions without error (about one per task). Add held-out tasks (or real outcomes) until then. Due:
  before relying on the active gate unattended.
- From the audit, deferred past Phase 7: F08 conflict refresh before retry, timeouts on git commands (the
  rest of F14), fencing for several owners of one project, event schema versioning, indexed board queries,
  CI.
