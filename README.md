# AgentSwarm

Independent teams of agents (an executor, an integration tester, a red team, a planner, ...) move work up a
ladder of maturity levels, from a branch per task to production, through gates. Teams never call each other:
each watches one level of a shared, append-only blackboard and reacts to its state, so there is nothing to
deadlock. **Language models produce, Jev decides, plain code controls**, and every one of Jev's decisions is
logged and scored against what turned out to be true.

- [tutorial/](tutorial/): the system one layer at a time, with a script to run for every step.
- [docs/REFERENCE.md](docs/REFERENCE.md): every config key, event, team, judge, report and command.
- [docs/PROMPTS.md](docs/PROMPTS.md): every prompt a model or Jev sees, generated from the code.
- [docs/MODELS.md](docs/MODELS.md): how models are configured, called and measured, and how Jev's answers are scaled.
- [FRAMEWORK_PROPOSAL.md](FRAMEWORK_PROPOSAL.md) and [docs/design/](docs/design/): the design and every
  decision made while building it; [docs/BACKLOG.md](docs/BACKLOG.md): what was postponed, and why.

Status: every phase of the build plan is done (teams and levels, Jev acting where it has earned it, documents
as a second domain, several projects at once), plus a hardening phase from an independent audit
([CODEX_AUDIT.md](CODEX_AUDIT.md)). One item is open: proving the active gate's false-promotion rate with more
evaluation data.

## Quick tutorial

The full [tutorial](tutorial/) has a script for every step (`tutorial/03_first_run.sh`, ...); here are the
essentials.

**1. Install.**

```bash
uv sync --all-extras
```

**2. A first run, with nothing else needed.** A fake executor writes each task to a note; the gate checks the
notes and merges them into `experimental`:

```bash
uv run agentswarm run examples/quickstart/project.yaml --root ~/agentswarm-runs/quickstart --task "Write a note"
```

```bash
git -C ~/agentswarm-runs/quickstart/repo log --oneline --graph --all
```

Every change is an event on the board (`blackboard.sqlite`), and every artifact is a branch merged with its
own merge commit.

**3. A real model, in a sandbox.** Pull a model that makes real tool calls, and build the sandbox image (code
a model wrote always runs in Docker, without network):

```bash
ollama pull gemma4:e4b
```

```bash
docker build -t agentswarm/sandbox-python:3.13 -f docker/sandbox-python.Dockerfile docker
```

```bash
uv run agentswarm run examples/greeter/evaluation/project.yaml --root ~/agentswarm-runs/greeter-1 --task "Add a function farewell(name) to greeter.py that returns 'Goodbye, <name>!', with tests."
```

Any LangChain provider works: change `model:` in the project config (`openai:...`, `anthropic:...`).

**4. Teams, a judge and a release train.** Put `TYPESAFE_API_KEY=...` in `.env` (git-ignored). The greeter
project uses the software preset: an executor, a gate judged by Jev (in shadow mode, logged but never
blocking), an integration tester and a red team, a release train to `candidate`, and a production gate that
asks a person:

```bash
uv run agentswarm run examples/greeter/project.yaml --root ~/agentswarm-runs/greeter-2 --task "Add two functions to greeter.py: farewell(name) and shout(name), with tests."
```

```bash
uv run agentswarm escalations examples/greeter/project.yaml --root ~/agentswarm-runs/greeter-2
```

**5. Measure the judge.** Run a task suite whose ground truth is automatic (hidden tests, mutation testing,
planted faults), then read the calibration, tune thresholds, and audit the gate with Jev acting:

```bash
uv run agentswarm evaluate examples/greeter/evaluation/suite.yaml --workdir ~/agentswarm-runs/eval-1
```

```bash
uv run agentswarm tune ~/agentswarm-runs/eval-1/decisions.sqlite
```

```bash
uv run agentswarm evaluate examples/greeter/evaluation/hard/suite.yaml --workdir ~/agentswarm-runs/eval-2 --active "gate:*"
```

```bash
uv run agentswarm audit ~/agentswarm-runs/eval-2/decisions.sqlite
```

**6. Documents, and several projects at once.** The same engine writes a handbook (draft → reviewed → final →
published, with reviewers whose every complaint must quote the text), and a swarm runs projects side by side
under one scheduler with a daily budget each:

```bash
uv run agentswarm swarm examples/swarm-quickstart.yaml --task "notes-a=Write a note" --task "notes-b=Write a note"
```

```bash
uv run agentswarm swarm examples/swarm-live.yaml --task "greeter=Add a function farewell(name) to greeter.py that returns 'Goodbye, <name>!'. Add tests." --task "handbook=Write a short handbook for running a book club"
```

`agentswarm status CONFIG --root DIR` shows a project's spend, escalations and history, even while it runs.

Project roots must be under your home folder, so Docker (Colima, Docker Desktop) can mount them.

## Development

```bash
uv run pytest
```

```bash
uv run ruff format . && uv run ruff check . && uv run pyright
```

Opt-in live tests: `-m docker`, `-m live` (with `AGENTSWARM_LIVE_MODEL=ollama:gemma4:e4b`), `-m jev` (with
`AGENTSWARM_JEV_MODEL=jev-latest`). Conventions for contributors and coding agents: [AGENTS.md](AGENTS.md).
