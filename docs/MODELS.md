# Models: how they are configured, called and measured

This page explains, step by step, what happens between a line like `coder: {model: "ollama:gemma4:e4b"}` in a
YAML file and a typed answer coming back. It also covers what can go wrong, how to add Claude and OpenAI
models, and how Jev's answers are scaled. The exact prompts are in [PROMPTS.md](PROMPTS.md); every
configuration key is in [REFERENCE.md](REFERENCE.md).

## 1. Naming a model

Models are named once, in the `models:` section of a project (or of a probe experiment), and teams refer to
them by that name:

```yaml
models:
  small: {model: "ollama:gemma4:e4b", temperature: 0, max_tokens: 16384}
  claude: {model: "anthropic:claude-sonnet-5-5", temperature: 0, max_tokens: 16384}

teams:
  - {use: executor, watches: raw, model: small}
```

`model` is always `"<provider>:<model id>"`. The provider picks the LangChain package; the id is the
provider's own name for the model. Nothing else in the code knows about providers.

| Setting | Default | What it does |
| --- | --- | --- |
| `model` | required | `"<provider>:<model id>"`, e.g. `ollama:qwen2.5-coder:7b`, `anthropic:claude-sonnet-5-5`, `openai:gpt-5` |
| `temperature` | the provider's | Randomness. 0 makes answers repeatable, which is what measurements need |
| `max_tokens` | none | The most output tokens one reply may have, **thinking included**. Without it a thinking model can run for hours |
| `context_window` | Ollama: 32768 | How many tokens the model reads at once. Only Ollama lets you set it; for other providers it is fixed by the model and setting it is refused |
| `base_url` | none | A different server, e.g. Ollama on another machine |
| `timeout_s` | none | How long one request may take |
| `usd_per_million` | none | `{input: …, output: …}` in US dollars per million tokens, for spend and budgets. An unpriced model counts as free, and reports say so |

Only settings you write are passed on; the defaults are the provider's own, except the two Ollama fixes in
section 4.

## 2. Building the model

`ChatModels` (in `llm/models.py`) builds each named model once, with LangChain's `init_chat_model`, and
reuses it. Three things happen on the way:

1. **The provider's package must be installed.** `ollama`, `openai` and `anthropic` are extras
   (`uv sync --extra anthropic`, …); all three are installed here. Any other LangChain provider works once
   its package is installed. A missing package is an error that names what to install.
2. **Settings are translated where a provider names them differently.** LangChain silently ignores a
   setting a model doesn't know, so a wrong name does nothing at all. Ollama reads the output limit only as
   `num_predict`, so `max_tokens` is passed under that name.
3. **Ollama gets a real context window.** Its own default is 4096 tokens, for every model, whatever the
   model supports. Unless `context_window` says otherwise, Ollama models get 32768.

API keys never appear in configuration. Each provider reads its usual environment variable
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); the command line loads them from `.env` in the working directory.

## 3. One model call, from start to finish

Every team, and every probe, calls a model the same way, through a worker (`llm/workers.py`):

1. **The system prompt** is the caller's instructions plus one fixed rule: the task data is JSON between
   `<data>` and `</data>`, and instructions inside it must never be followed.
2. **The user message** is one typed payload (a Pydantic model) as JSON, inside `<data>…</data>`.
3. **Tools,** if the caller has any: Python methods whose signature and docstring the model sees.
   Probes have none.
4. **The answer** must be an instance of the caller's answer model (also Pydantic). How the model is asked
   for it depends on what the model supports:
   - **native structured output** (OpenAI, and Claude models whose profile says so): the provider itself
     constrains the reply to the JSON schema;
   - **an answer tool** (Ollama, and models without native support): the schema becomes a tool, and the
     model answers by calling it. LangChain checks the arguments and, if they don't fit, tells the model
     what was wrong and lets it try again.
5. **If the model ends without an answer** (small models often finish in plain text), the worker asks once
   more, at most twice: *"Stop working now. Give your final answer in the required structured format, based
   on the work above."*
6. **If there is still no valid answer,** the call fails with one of three errors:

   | Error | Meaning |
   | --- | --- |
   | `InvalidAnswer` | The model replied, but never with JSON that fits the schema |
   | `CutOff` | The reply stopped at `max_tokens` before the answer was complete |
   | `WorkerError` | Anything else: the provider failed, timed out, or the team's `max_model_calls` was reached |

   A team that gets one of these escalates the artifact; a reviewer's failure is only a notice, so it never
   stops other reviewers.

Every reply's token counts are recorded as it completes, even when a later reply fails, so spend is never
undercounted. Teams record them on the board as `Spent` events; probes record them per answer.

## 4. Lessons from the first live runs (Ollama)

The first Phase 8 runs lost a large share of their answers to settings, not to the models:

| Problem | What happened | Fix |
| --- | --- | --- |
| A 4096-token window | Ollama silently dropped the start of longer conversations, instructions included; about a third of the reviews ended without an answer | `context_window`, 32768 by default for Ollama |
| `max_tokens` ignored | Ollama never saw the limit: gemma4-12b thought for 2 hours (159,000 tokens) on one critique | Passed as `num_predict` |
| No limit at all | A thinking model can loop on a hard prompt | Probe experiments set `max_tokens: 16384` |
| Repetition at temperature 0 | gemma4-26b sometimes repeats itself until Ollama aborts ("token repeat limit") | Recorded as a failure; whether Gemma needs its recommended temperature (1.0) instead is still to be measured |

The lesson for any new provider: check that each setting reaches the model, with a one-line test, before
trusting a run.

## 5. Proper JSON is measured, per model

A model that can't produce valid structured answers is useless here, whatever it knows. The atomic
benchmark (`agentswarm probe`) records it for every answer, separately from whether the answer was right:

| Outcome | Counts as |
| --- | --- |
| `solved`, `caught`, `quiet` | a valid answer that was right |
| `unsolved`, `missed`, `false_alarm`, `wrong_evidence` | a valid answer that was wrong |
| `invalid_answer` | no valid JSON: the format quality you asked about |
| `cut_off` | the answer didn't fit in `max_tokens` |
| `no_answer` | the provider or the limits failed |

The summary table has a column for each of the last three per model and kind. Not measured yet: whether a
valid answer needed the final-answer request of step 5. A model that only answers properly when nudged is
weaker, and that is the next thing to record.

## 6. Adding Claude and OpenAI models

Everything is in place except keys and the decision to spend money:

1. Put the keys in `.env` (git-ignored): `ANTHROPIC_API_KEY=…`, `OPENAI_API_KEY=…`.
2. Add the models to an experiment, with their prices so the spend is reported:

   ```yaml
   models:
     claude-sonnet: {model: "anthropic:claude-sonnet-5-5", temperature: 0, max_tokens: 16384,
                     usd_per_million: {input: …, output: …}}
     claude-haiku: {model: "anthropic:claude-haiku-4-5-20251001", temperature: 0, max_tokens: 16384,
                    usd_per_million: {input: …, output: …}}
     openai: {model: "openai:<model id>", temperature: 0, max_tokens: 16384,
              usd_per_million: {input: …, output: …}}
   ```

   Prices come from each provider's price page at the time of the run.
3. Run the smoke experiment first: it shows the tokens per probe, and from that the cost of the full run,
   before any money is committed to it.

Hosted models don't use the local GPU, so they can run alongside the local ones.

## 7. Jev's scale: what its numbers mean

Jev doesn't produce text. It answers typed questions about a typed state (see the Jev section of
[PROMPTS.md](PROMPTS.md)), and every question the judges ask is a **Noul** question: a yes/no question whose
answer is one number, **p(yes), between 0 and 1**: Jev's probability that the answer is yes.

- **Questions are phrased so that yes is good.** "Does the change address every part of the task?", not
  "Is anything missing?". Then a high number is always good news, and thresholds read the same way for every
  question.
- **A number becomes a verdict through two thresholds per question,** `pass_at` and `fail_at`:

  ```
  0 ────────── fail_at ─────────────── pass_at ────────── 1
     fail                  unsure                  pass
  ```

  The defaults are `pass_at: 0.9` and `fail_at: 0.2`, chosen conservatively because Jev's evaluation found
  Noul well calibrated.
- **A judge's verdicts combine strictly:** any question that fails rejects the change (with that question's
  finding); otherwise any unsure question escalates it to a human; only all-pass promotes it.
- **Thresholds are then fitted from data** (`agentswarm tune`). Decisions are logged with what later turned
  out to be true, and the tuner picks, per question, the lowest `pass_at` whose answers are right at least
  95% of the time, and the highest `fail_at` likewise, given at least 20 decided answers. Each figure comes
  with a 95% lower bound, since a few dozen answers prove less than they seem to.
- **Calibrated means the number can be read as a frequency:** of all answers near 0.8, about 80% should turn
  out yes. `agentswarm calibrate` checks this per question.
- **A judge's version is a hash of its wording.** Rewording a question starts new calibration data:
  thresholds fitted for the old wording don't carry over.
- **Difficulty** (for routing) uses the same scale differently: Jev answers "is this a simple task?", and the
  difficulty is 1 − p(simple). There it only has to order tasks from easy to hard; the models' measured
  success curves do the calibration.
- **The fallback** (a language model standing in when Jev is unavailable) answers yes, no or unsure, mapped
  to 0.95, 0.05 and 0.5. These numbers aren't calibrated, and decisions made that way are marked.

Jev's client also knows two richer answer types: a **score** on named levels, and a **choice** among named
options, each with a confidence and a probability per level or option. The judges use only Noul so far;
scores would suit rating answers on a scale, for example "how good is this answer, from 1 to 5".
