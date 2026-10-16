"""Project configuration: one YAML file per project, validated on load (proposal §17)."""

import os
import urllib.parse
from collections.abc import Mapping, Sequence
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Any, ClassVar, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from agentswarm.core.domain import AttackFocus, FindingKind, Level, Usage


class Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LevelLadder(Strict):
    """The ordered maturity levels of a domain, lowest first.

    The lowest level holds artifacts on their own short-lived branches (`raw/<id>-<slug>`); every level
    above it is a long-lived branch of the same name. New artifacts fork from the second level.
    """

    levels: tuple[Level, ...] = Field(min_length=2)

    @field_validator("levels")
    @classmethod
    def _unique(cls, levels: tuple[Level, ...]) -> tuple[Level, ...]:
        if len(set(levels)) != len(levels):
            raise ValueError(f"levels must be unique: {levels}")
        return levels

    @property
    def lowest(self) -> Level:
        return self.levels[0]

    @property
    def base(self) -> Level:
        """The level new artifacts fork from."""
        return self.levels[1]

    @property
    def branch_levels(self) -> tuple[Level, ...]:
        """The levels backed by a long-lived branch."""
        return self.levels[1:]

    def contains(self, level: Level) -> bool:
        return level in self.levels

    def reached(self, level: Level, target: Level) -> bool:
        """Whether `level` is `target` or above it."""
        return self.levels.index(level) >= self.levels.index(target)

    def next(self, level: Level) -> Level | None:
        i = self.levels.index(level)
        return self.levels[i + 1] if i + 1 < len(self.levels) else None


class Domain(StrEnum):
    SOFTWARE = "software"
    DOCUMENT = "document"


class JevMode(StrEnum):
    SHADOW = "shadow"
    ACTIVE = "active"


class SandboxKind(StrEnum):
    DOCKER = "docker"  # the default: anything a model wrote runs isolated
    LOCAL = "local"  # on the host; only for tests and trusted checks


class SandboxConfig(Strict):
    """Where hard checks and the executor's test runs execute (proposal §13.4)."""

    kind: SandboxKind = SandboxKind.DOCKER
    image: str = "agentswarm/sandbox-python:3.13"  # built from docker/sandbox-python.Dockerfile
    network: bool = False
    cpus: float = Field(default=2.0, gt=0)
    memory: str = "2g"
    pids: int = Field(default=256, gt=0)


class Price(Strict):
    """US dollars per million tokens. Cached input has its own prices; without them it is plain input."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(default=None, ge=0)  # e.g. Anthropic: a tenth of input
    cache_write: float | None = Field(default=None, ge=0)  # e.g. Anthropic: 1.25 times input

    def usd(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / 1_000_000

    def usd_of(self, usage: Usage) -> float:
        """What the calls cost: what the provider charged when it reported that for each call, else the
        estimate from these prices (a router such as OpenRouter may choose a host that charges more)."""
        if usage.fully_billed:
            return usage.billed_usd
        read = self.cache_read if self.cache_read is not None else self.input
        write = self.cache_write if self.cache_write is not None else self.input
        plain = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
        cents = plain * self.input + usage.cache_read_tokens * read + usage.cache_write_tokens * write
        return (cents + usage.output_tokens * self.output) / 1_000_000


class JevConfig(Strict):
    model: str = "jev-latest"  # pin a concrete version (e.g. "jev-1.13.0") once thresholds are calibrated
    mode: JevMode = JevMode.SHADOW  # the default for decision points `active` doesn't name
    # Decision points that act on Jev's ruling, as glob patterns over point names, e.g.
    # "gate:raw->experimental", "relevance:*", "size:raw". Promote a point once it is calibrated (§12.3).
    active: tuple[str, ...] = ()
    fallback: str | None = None  # a model in `models` that answers when Jev can't (uncalibrated, marked)
    usd_per_million: Price | None = None  # Jev's price, for spend and budgets
    timeout_s: float = Field(default=30.0, gt=0)


class CheckConfig(Strict):
    """A deterministic hard check: a command run in the sandbox on the artifact's files. Exit 0 passes."""

    command: tuple[str, ...] = Field(min_length=1)
    timeout_s: float = 600.0
    kind: FindingKind = FindingKind.TEST_FAILURE  # the finding emitted when it fails
    # Exit codes meaning "ran, and found a problem" (pytest: 1). Any other non-zero exit, a timeout or a
    # command that can't start is an error running the check: it still blocks a gate, but is never
    # evidence of a defect. Unset: every non-zero exit is a failure (CODEX_AUDIT.md F13).
    failure_exit_codes: tuple[int, ...] | None = None
    # A ratchet (gating/ratchet.py, pytest only): instead of "everything passes", the work must make at least
    # one more of this level's failing tests pass and break none that passed there.
    ratchet: Level | None = None
    # With a ratchet: whether the work must make a failing test pass. False for refactoring, which may change
    # no behaviour: then breaking nothing is enough.
    progress: bool = True
    # With a ratchet: only tests under these paths (the independent acceptance tests) count as broken when
    # they stop passing or disappear; the rest are the producer's own, which it may rewrite or delete, though
    # none may fail. Empty: every test is protected.
    protected: tuple[str, ...] = ()


class Threshold(Strict):
    """Per question: p(yes) ≥ pass passes, p(yes) ≤ fail fails, anything between is unsure."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    pass_at: float = Field(validation_alias="pass", ge=0.0, le=1.0)
    fail_at: float = Field(validation_alias="fail", ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _fail_below_pass(self) -> Self:
        if self.fail_at >= self.pass_at:
            raise ValueError(f"fail ({self.fail_at}) must be below pass ({self.pass_at})")
        return self


class GateConfig(Strict):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    from_level: Level = Field(alias="from")
    to_level: Level = Field(alias="to")
    hard_checks: tuple[str, ...] = ()  # names of entries in ProjectConfig.checks, run in order
    judge: str | None = None  # name of a judge spec; used from phase 2
    thresholds: dict[str, Threshold] = Field(default_factory=dict[str, Threshold])
    requires_human: bool = False
    requires: tuple[
        str, ...
    ] = ()  # teams whose sign-off every promoted artifact needs (gates above the base)


class ModelConfig(Strict):
    """A chat model from any provider LangChain supports, chosen by the user, e.g. "ollama:qwen2.5-coder:7b",
    "openai:gpt-5" or "anthropic:claude-sonnet-4-6". The provider's package must be installed as an extra.
    API keys come from the provider's usual environment variables, never from this file."""

    model: str = Field(pattern=r"^[A-Za-z0-9_\-]+:.+$")  # "<provider>:<model name>"
    temperature: float | None = None
    max_tokens: int | None = None
    base_url: str | None = None  # e.g. a remote Ollama server, or an OpenAI-compatible service
    # The environment variable holding the key for `base_url`, e.g. OPENROUTER_API_KEY: its name, not the key.
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_s: float | None = None
    context_window: int | None = Field(default=None, gt=0)  # tokens the model reads at once; see ChatModels
    thinking: bool | None = None  # a thinking model's reasoning on or off; None: the provider's default
    usd_per_million: Price | None = None  # for spend and budgets; unpriced models cost 0 (and say so)

    # Providers that cache prompts (OpenRouter is reached through "openai"): their entries name the cached-
    # input price, or cached input would be counted at full price (GPT-6.1 Sol was counted about 5 times too
    # high on 2026-10-09, which misled the routing).
    CACHING: ClassVar[frozenset[str]] = frozenset({"openai", "anthropic"})

    @model_validator(mode="after")
    def _cached_price_named(self) -> Self:
        price = self.usd_per_million
        if price is not None and price.cache_read is None and self.provider in self.CACHING:
            raise ValueError(
                f"{self.model}: set usd_per_million.cache_read, the price of cached input (equal to `input` "
                "when the model has no cache discount); without it cached input counts at the full price"
            )
        return self

    @property
    def provider(self) -> str:
        return self.model.split(":", 1)[0]

    @property
    def account(self) -> str:
        """Who bills for it: the host of `base_url` (openrouter.ai), else the provider (anthropic)."""
        if self.base_url is None:
            return self.provider
        host = urllib.parse.urlparse(self.base_url).hostname
        return host or self.provider


class RoutingConfig(Strict):
    """Choose a team's starting model tier from outcomes so far (Phase 8, teams/routing.py)."""

    ledger: Path | None = None  # the shared record of outcomes; none: only log what Jev says
    target: float = Field(default=0.8, gt=0.0, le=1.0)  # start on the cheapest tier drawn at least this good
    # An outcome counts half once this many later calls of its kind are recorded (discounted Thompson
    # sampling, by activity rather than time, so a pause forgets nothing); None: all count fully
    half_life_calls: float | None = Field(default=200.0, gt=0)
    # A model whose estimated cost per attempt is above this starts no step until it has a few measured
    # outcomes (it can still be reached by the cascade): bounded exploration of expensive models
    explore_usd: float | None = Field(default=None, gt=0)


class TeamConfig(Strict):
    use: str  # name in the team catalog
    watches: Level
    model: str | None = None  # name of an entry in ProjectConfig.models
    model_tiers: tuple[
        str, ...
    ] = ()  # instead of `model`: cheap first, moving up a tier per rejection (§13.3)
    checks: tuple[str, ...] = ()  # checks the team may run while working, by name
    max_model_calls: int = Field(default=40, gt=0)  # per act; the agent stops when it is reached
    # executors: independent attempts per action; the first that passes its checks is kept
    attempts: int = Field(default=1, ge=1)
    # executors with model_tiers: each failed attempt moves one tier up (a cascade: cheap models try first,
    # the strong one takes only what they failed), instead of retrying the same model
    climb: bool = False
    # with climb: a step every model has failed goes back to the decomposer to be split (ADaPT), instead of
    # to a person; needs a decomposer team
    split_on_failure: bool = False
    # stateless builders: model answers per attempt at most (plain code runs the loop between them)
    rounds: int = Field(default=20, ge=1)
    # executors: when its checks fail, the model keeps its work and is told what failed, this many times
    repairs: int = Field(default=0, ge=0)
    # executors: work that passes its checks is committed even when the model calls it incomplete (worklist
    # items, where a ratchet gate judges progress and partial progress is the point)
    partial: bool = False
    # planners: split when Jev's p(fits) is at most this, not only when it is clearly low
    split_below: float | None = Field(default=None, gt=0.0, lt=1.0)
    # planners: at max_depth, build a task that still seems too large instead of escalating it
    build_at_max_depth: bool = False
    constraints: tuple[str, ...] = ()  # extra rules given to the model with every task
    relevance: str | None = None  # a Jev relevance judge asked before working (analyzer teams)
    max_depth: int = Field(default=3, ge=1)  # decomposition: how deep tasks may be split
    routing: RoutingConfig | None = None  # executors: Jev describes the task, the router picks the tier
    # executors, projects without labels: a model (another family than builder and check writer) that rules
    # when the builder disputes an acceptance test, against the spec files named in `spec`
    referee: str | None = None
    spec: tuple[str, ...] = ()  # with a referee: the spec, as files in the project (e.g. docs/toml.md)
    # acceptance testers, projects without labels: checks that need no hand-computed answers (round trips,
    # equivalent inputs, rules the spec states) instead of exact expected values
    derived: bool = False

    @model_validator(mode="after")
    def referee_needs_spec(self) -> Self:
        if self.referee is not None and not self.spec:
            raise ValueError("a referee needs `spec`: the files a disputed test is judged against")
        return self


class HardeningConfig(Strict):
    """Red teams attack the merged product, one spec section and focus at a time, until a round confirms
    nothing new (docs/design/hardening.md). Each confirmed failure becomes a fix task with that test."""

    model: str  # the red teams' model, a name in `models`
    referee: str  # rules on each report against the spec before it becomes a task, a name in `models`
    spec: str  # the spec file in the project, split into sections at `heading`
    heading: str = "## "  # a line starting with this opens a section
    sections: tuple[str, ...] = ()  # section titles to attack, in this order; empty: every section
    section_size: int = Field(default=10_000, gt=0)  # a longer section is cut at its next deeper heading
    check: str  # the test check (a name in `checks`) the red teams' tests and the mutants run with
    focuses: tuple[AttackFocus, ...] = tuple(AttackFocus)
    max_problems: int = Field(default=3, ge=1)  # reports taken from one attack
    mutants: int = Field(default=20, ge=1)  # mutants tried per mutation attack, for leads
    # done after this many whole rounds in a row confirm nothing: one quiet round is weak evidence (the user,
    # 2026-10-15: "like preparing for a hurricane three times, and at the fourth you don't care")
    quiet_rounds: int = Field(default=2, ge=1)
    max_model_calls: int = Field(default=30, gt=0)  # per attack


class SupervisorConfig(Strict):
    """When the supervisor steps in (§14.1)."""

    stall_after_minutes: float = Field(default=30.0, gt=0)  # no event on an artifact for this long
    max_rejections: int = Field(default=3, ge=1)  # rejected this many times in a row
    drift_every: int = Field(default=5, ge=1)  # ask about drift after every N changes merged


class SpendLimits(Strict):
    """Spending limits for every run on this machine together, per UTC day (control/limits.py).

    Read from `~/agentswarm-data/limits.yaml` (or the file `AGENTSWARM_LIMITS` names; "off" for none), so they
    hold whichever project, experiment or command spends. Accounts are who bills: e.g. `anthropic`,
    `openrouter.ai`, `openai`.
    """

    ledger: Path = Path("~/agentswarm-data/spend.sqlite")  # every priced model call of every run
    usd_per_day: float | None = Field(default=None, gt=0)  # all accounts together
    per_account: dict[str, float] = Field(default_factory=dict[str, float])

    FILE: ClassVar[Path] = Path("~/agentswarm-data/limits.yaml")
    VARIABLE: ClassVar[str] = "AGENTSWARM_LIMITS"

    @classmethod
    def load(cls) -> "SpendLimits | None":
        value = os.environ.get(cls.VARIABLE, "").strip()
        if value == "off":
            return None
        path = Path(value).expanduser() if value else cls.FILE.expanduser()
        if not path.is_file():
            if value:
                raise ValueError(f"{cls.VARIABLE}={value}: no such file")
            return None
        limits = cls.model_validate(yaml.safe_load(path.read_text()) or {})
        return limits.model_copy(update={"ledger": limits.ledger.expanduser()})


class Budgets(Strict):
    usd_per_day: float | None = None
    # No new action starts unless this much of the day's budget is left: the budget is checked between
    # actions, so an action that starts just under the limit can overshoot it by its own cost.
    reserve_usd: float = Field(default=0.0, ge=0.0)
    max_rounds_per_artifact: int | None = None


class Presets:
    """Domain presets shipped with the package (agentswarm/core/presets/<domain>.yaml)."""

    PACKAGE = "agentswarm.core.presets"

    @classmethod
    def for_domain(cls, domain: str) -> dict[str, Any]:
        resource = files(cls.PACKAGE) / f"{domain}.yaml"
        if not resource.is_file():
            return {}
        return cast(dict[str, Any], yaml.safe_load(resource.read_text()))

    @classmethod
    def apply(cls, data: Mapping[str, Any]) -> dict[str, Any]:
        """The preset for data's domain, with data's own top-level keys replacing the preset's."""
        domain = data.get("domain")
        preset = cls.for_domain(str(domain)) if domain is not None else {}
        return preset | dict(data)


class ModelOverride:
    """One model for every role of every project loaded: a baseline run without editing configs.

    `AGENTSWARM_MODEL` names a YAML file holding one model entry (e.g. examples/models/haiku.yaml). Whether a
    failure means the framework or the model is too weak is only known once a capable model has been tried.
    """

    VARIABLE = "AGENTSWARM_MODEL"

    @classmethod
    def path(cls) -> Path | None:
        value = os.environ.get(cls.VARIABLE, "").strip()
        return Path(value).expanduser() if value else None

    @classmethod
    def from_env(cls) -> "ModelConfig | None":
        path = cls.path()
        if path is None:
            return None
        if not path.is_file():
            raise ValueError(f"{cls.VARIABLE}={path}: no such file")
        return ModelConfig.model_validate(yaml.safe_load(path.read_text()))


class WorklistConfig(Strict):
    """Failing tests become work items (control/worklist.py; ROADMAP.md stage 1)."""

    check: str  # a ratchet check (`checks.<name>.ratchet`), whose failing tests on the level are the work
    max_open: int = Field(default=1, ge=1)  # items being worked on at once
    attempts_per_file: int = Field(default=3, ge=1)  # items per test file before it is left to a person
    shown: int = Field(default=10, ge=0)  # failing test ids named in an item's task
    only: tuple[str, ...] = ()  # glob patterns: only these test files become items (e.g. one hard section)


class ScoreConfig(Strict):
    """The definition of done as numbers: a test check run on the first shared level at the start and end of
    every run, with tests the agents see and held-out ones they don't (evaluation/score.py)."""

    check: str  # a test check (`checks.<name>`) whose command runs pytest
    seen: str = "tests/acceptance/"  # where the tests the agents see live in the repository
    held_out: tuple[Path, ...] = ()  # folders of tests no agent sees, copied in only to score


class QuestionsConfig(Strict):
    """A document's definition of done as numbers: questions no agent sees, answered by a reader model from
    the document alone and graded against known answers (evaluation/questions.py)."""

    path: Path  # the questions file, e.g. ~/agentswarm-data/guide/out/held_out/questions.json
    reader: str  # a name in `models`: answers from the document alone
    grader: str  # a name in `models`: compares each answer with the references


class ProjectConfig(Strict):
    project: str
    domain: Domain
    north_star: str
    levels: LevelLadder
    seed: Path | None = None  # a directory whose files form the repository's first commit
    sandbox: SandboxConfig = SandboxConfig()
    models: dict[str, ModelConfig] = Field(default_factory=dict[str, ModelConfig])
    checks: dict[str, CheckConfig] = Field(default_factory=dict[str, CheckConfig])
    teams: tuple[TeamConfig, ...] = ()
    gates: tuple[GateConfig, ...] = ()
    budgets: Budgets = Budgets()
    supervisor: SupervisorConfig = SupervisorConfig()
    jev: JevConfig = JevConfig()
    worklist: WorklistConfig | None = None  # failing tests of a ratchet check become work items
    score: ScoreConfig | None = None  # measured at the start and end of every run
    hardening: HardeningConfig | None = None  # red teams attack the product until nothing new is found
    questions: QuestionsConfig | None = None  # a document's hidden questions, measured like `score`

    @model_validator(mode="before")
    @classmethod
    def _with_preset(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return Presets.apply(cast(Mapping[str, Any], value))
        return value

    @field_validator("levels", mode="before")
    @classmethod
    def _ladder_from_list(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return {"levels": cast(Sequence[object], value)}
        return value

    @field_serializer("levels")
    def _ladder_as_list(self, ladder: LevelLadder) -> list[str]:
        return list(ladder.levels)  # written back the way it is written in YAML

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        uses = [t.use for t in self.teams]
        if len(set(uses)) != len(uses):
            raise ValueError(f"each team may appear once (its name is its `use`): {uses}")
        for team in self.teams:
            if not self.levels.contains(team.watches):
                raise ValueError(f"team {team.use!r} watches unknown level {team.watches!r}")
            if self.jev.fallback is not None and self.jev.fallback not in self.models:
                raise ValueError(f"jev.fallback names undefined model {self.jev.fallback!r}")
            if team.model is not None and team.model_tiers:
                raise ValueError(f"team {team.use!r}: set `model` or `model_tiers`, not both")
            for model in (team.model, *team.model_tiers, team.referee):
                if model is not None and model not in self.models:
                    raise ValueError(f"team {team.use!r} uses undefined model {model!r}")
            for name in team.checks:
                if name not in self.checks:
                    raise ValueError(f"team {team.use!r} uses undefined check {name!r}")
        for gate in self.gates:
            for level in (gate.from_level, gate.to_level):
                if not self.levels.contains(level):
                    raise ValueError(f"gate uses unknown level {level!r}")
            if self.levels.next(gate.from_level) != gate.to_level:
                raise ValueError(f"gate {gate.from_level} -> {gate.to_level} must connect adjacent levels")
            for name in gate.hard_checks:
                if name not in self.checks:
                    raise ValueError(
                        f"gate {gate.from_level} -> {gate.to_level} uses undefined check {name!r}"
                    )
            self._check_gate_level_rules(gate)
        for team in self.teams:
            if team.split_on_failure and not (team.climb and any(t.use == "decomposer" for t in self.teams)):
                raise ValueError(f"team {team.use!r}: split_on_failure needs climb and a decomposer team")
        if self.score is not None and self.score.check not in self.checks:
            raise ValueError(f"score uses undefined check {self.score.check!r}")
        if self.questions is not None:
            if self.score is not None:
                raise ValueError(
                    "a project is scored by tests (`score`) or by questions (`questions`), not both"
                )
            for model in (self.questions.reader, self.questions.grader):
                if model not in self.models:
                    raise ValueError(f"questions use undefined model {model!r}")
        if self.hardening is not None:
            for model in (self.hardening.model, self.hardening.referee):
                if model not in self.models:
                    raise ValueError(f"hardening uses undefined model {model!r}")
            if self.hardening.check not in self.checks:
                raise ValueError(f"hardening uses undefined check {self.hardening.check!r}")
        return self

    def _check_gate_level_rules(self, gate: GateConfig) -> None:
        name = f"gate {gate.from_level} -> {gate.to_level}"
        for team in gate.requires:
            if not any(t.use == team and t.watches == gate.from_level for t in self.teams):
                raise ValueError(
                    f"{name} requires {team!r}, which is not a team watching {gate.from_level!r}"
                )
        if gate.from_level == self.levels.lowest:
            if gate.requires:
                raise ValueError(f"{name} judges single changes; only gates above it can require sign-offs")
        elif gate.judge is not None:
            raise ValueError(
                f"{name}: only the gate out of the lowest level has a judge; gates above it promote on "
                "sign-offs, hard checks and people (see docs/BACKLOG.md)"
            )

    def gate_from(self, level: Level) -> GateConfig | None:
        return next((g for g in self.gates if g.from_level == level), None)

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load a config file. Relative `seed` and routing `ledger` paths are resolved against its folder.

        With `AGENTSWARM_MODEL` set, every model the config names is replaced by that one (`ModelOverride`).
        """
        text = path.read_text()
        raw = yaml.safe_load(text)
        config = cls.model_validate(raw)
        override = ModelOverride.from_env()
        if override is not None:
            config = config.model_copy(update={"models": {name: override for name in config.models}})
        base = path.parent
        if config.seed is not None and not config.seed.is_absolute():
            seed = (base / config.seed.expanduser()).resolve()
            config = config.model_copy(update={"seed": seed})
        teams = tuple(cls._resolve_curves(team, base) for team in config.teams)
        return config.model_copy(update={"teams": teams})

    @staticmethod
    def _resolve_curves(team: TeamConfig, base: Path) -> TeamConfig:
        routing = team.routing
        if routing is None or routing.ledger is None:
            return team
        ledger = (base / routing.ledger.expanduser()).resolve()
        resolved = routing.model_copy(update={"ledger": ledger})
        return team.model_copy(update={"routing": resolved})
