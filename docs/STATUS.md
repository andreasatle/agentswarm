# Where we are (2026-11-09, with what is current on 2026-11-20 first)

Details live in the docs named.

## Now (2026-11-20)

- **Self-correction benchmark (docs/design/self-correction.md):** the typed writer (structured output, numbered references) measured in round 2: no quality gain over round 0, about 1.6 times the cost. Its causes were fixed on 2026-11-17 (f67254b): the length is a target with 20% slack (the user: "a guideline"), a reviewer's findings on a section go to one revision, a revision that loses most of a section is refused, OpenAI-style models keep no connections between event loops ("Event loop is closed"), unfinished benchmark runs are marked. **Next: round 3 on the three cases, about $4-5, waiting for the user's yes.**
- **Reasoning referee:** reader findings are ruled by `referee-fast` (DeepSeek V4 Pro with reasoning off through OpenRouter); cited statements keep the reasoning referee.
- **The data-governance essay, rerun and published (2026-11-20)** (~/agentswarm-runs/data-governance-2, page.html beside it): 3,014 words, $3.33 of $12. Audit 55% fully supported after writing, 65% after two review rounds (45% in the first run); round 2 still upheld 2-4 claim findings per section, so the fixes don't converge yet. No conflicts, no event-loop errors, GPT-Sol once ($0.13). Cost: DeepSeek referees $1.40 and audit $0.37 (53%), Haiku writer $0.88 on 6.8M input tokens (26%; the target for writers on passages), Haiku reviewer and claim checker $0.44.
- **Daily backstop** in ~/agentswarm-data/limits.yaml is 8.0, raised for 2026-11-14; the user is to set it back to 5.0.

## What works

- **Building software to a definition of done** (stages 1-3, docs/ROADMAP.md): CommonMark, TOML, jq, YAML 1.2 and HTML5 from empty stubs, 87-100% of held-out tests; self-supervised TOML, YAML and JSONPath (every test hidden) 97-100%.
- **Hardening by red teams** (docs/design/hardening.md): jq 87% to 94% held out over three rounds and the combination focus; YAML-self 97% to 98%. Diminishing returns per round.
- **Documents** (docs/design/documents.md): briefer, one-call section writers, author-year citations with pages, perspective reviewers (facts, coverage, reader, consistency) and a citation audit. Measured on Qasper: perspectives suggestive, not proven (19 against 15-18 of 36).
- **One command from topic to document (goal 13, docs/design/pipeline.md):** `agentswarm write / runs / approve`, a LangGraph graph over plain stages that pauses for the user's approval of the sources and the publishing; settings in pipeline/policy.yaml. First real run 2026-11-01: how sleep affects memory, $1.57 of a $1.60 budget, finished and approved; not on the homepage (only `agentswarm publish NAME` puts it there, when the user says so). What that run needed is listed in the design doc.
- **The studio** (`agentswarm studio`): document runs started, followed and answered in a browser on localhost, with the board view.
- **The run record (2026-11-11, removed 2026-11-12, docs/design/run-record.md):** each model call's spend records its model and prompt version; the join command and per-statement verdict events were removed under goal 15 when GEPA's prompt, their planned use, was not accepted.
- **Error correlation (2026-11-11, docs/design/error-correlation.md):** Haiku's two checker arms are nearly one checker (wrong together on 18 of ~20 errors); other families overlap moderately (phi 0.2-0.4); after a failed routed call the next attempt fails 74-81% whichever model makes it, against 33% in general: failures sit on tasks more than on models.
- **Real use:** `import-docs` (the Challenger report), `learn` (tutorials from papers: JEPA, test-time compute, mechanistic interpretability), `find-papers`, the Venice guide from Wikipedia; the tutorials are on the homepage.

## Cost (the user's top priority, 2026-11-09)

- **The user: "I don't want to spend $10-20 every day I'm working on this."** The machine's daily backstop (`~/agentswarm-data/limits.yaml`) is now $5, lowered from $21. Every paid run still needs the user's yes, with its expected cost and the day's spend.
- **Where the money went:** $132 over 13 working days in October (about $10 a day), mostly software builds (CommonMark $40, jq $23, YAML $14). A document run is about $1.50, of which the perspective reviewers (an expensive model, 25-55k tokens per call) are about 60%.
- **Research on cheaper review** (docs/RESEARCH.md section 14): long context still reads slightly better than retrieval at our input sizes; the established cheap structure for checking a text against sources is claim by claim (split, retrieve per claim, verify with a small model such as MiniCheck), with a fallback to the full source (Self-Route). Not decided.

- **Claude through OpenRouter is now cached (2026-11-09):** haiku55's writers and executors sent 137M input tokens with none cached, because the Anthropic caching middleware ignores a model reached through "openai"; requests to OpenRouter for `anthropic/` models now carry a top-level `cache_control` (llm/models.py). Then Haiku moved to Anthropic's own API (the user, 2026-11-09: no reason to pay OpenRouter's fee), in the document template, the examples and the project configs in ~/agentswarm-data, where the existing caching middleware applies; the OpenRouter fix stays for any Claude model still routed there. Confirmed live: the second of two identical calls read 4,998 of 5,012 input tokens from the cache.
- **Audit A04 done (2026-11-09):** each executor attempt's spend carries the attempt's id, so the router learns each attempt's real cost.
- **Audit A03 and A06 done (2026-11-09):** every model call is admitted against the budget first with its worst case, so an approved budget is a cap (a refused call's work waits, and the supervisor says why); the runner rechecks pause and budget before every action. Open: nothing is reserved while a call runs, so concurrent runs can share headroom (BACKLOG.md).

## Measured weak spots

- **Citations:** about 75% of cited statements fully supported after one facts review (65% before); most of the rest overstate.
- **The checkers (2026-11-11, docs/design/documents.md):** against careful labels of 50 statements, the claims checker catches 70% of problems and passes one wrong statement in ten (a ceiling near 92%); the citation audit we use as the quality meter catches 65% and 39% of its flags are fine, so audit percentages are noisy.
- **Downloads:** publishers refuse most programs; on sleep and memory none of five picks downloaded, the user fetched them by hand. Repository copies (PubMed Central, arXiv) are reliable; "bronze" open access is not. Goal 14 is the real fix.

## Open, in order

1. **Cheaper runs:** Haiku cached and on Anthropic's API; document reviews check each cited statement with Haiku (the A/B, docs/design/documents.md: 19 of 23 problems caught for $0.16, against 5 of 23 for $0.20 by the facts reviewer; $0.07 a section a round, down from $0.18). A trial round end to end (2026-11-09, docs/design/documents.md) found the referee rejecting real problems, ruling without the source; it now reads each claims finding against its cited part; the rerun: referee agrees with the audit on 11 of 12, one round takes sections 1-4 from 76% to 83% fully supported, at $0.15 a section a round (twice the estimate; levers in the design doc). Third trial (2026-11-10), levers built: the checker costs a third, the referee is now 75% of a round ($0.017 a finding), bundled revisions fixed 2 of 4 sections (one broke the length cap, one made no changes on Haiku). Fourth trial, the writer's cascade fixed (it had never been wired: no climb, no repairs in any document run): one round takes sections 1-4 from 76% to 86% fully supported, about $0.11 a section; a second round would reach about 91%; document runs now review two rounds, whenever the audit's sample is below 90% (the user, 2026-11-10); the sleep tutorial reviewed in full: sampled audit 60% -> 90% for $0.97. Next by spend: the software builders (60% of past spend) when software runs resume.
2. **Later:** the rest of A02 (product code in the test process) when there is any sign of tampering; A05; goal 14 (research from the open web); stage 4, when a swarm is competitive (waits until the framework behaves well).

## Standing rules to remember

- No standing budget: each paid run gets its expected cost and the day's spend, and the user decides (memory: spending-levels). The $5 daily limit is a backstop, not a budget to fill.
- Chat answers short, main message first; the user learns by asking.
- Nothing runs unattended while the user is away.
