"""Project configuration: one YAML file per project, validated on load (proposal §17)."""

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentswarm.domain import FindingKind, Level


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


class JevConfig(Strict):
    model: str = "jev-latest"  # pin a concrete version (e.g. "jev-1.13.0") once thresholds are calibrated
    mode: JevMode = JevMode.SHADOW  # shadow: judge and log only; active: gates act on the ruling
    timeout_s: float = Field(default=30.0, gt=0)


class CheckConfig(Strict):
    """A deterministic hard check: a command run in the sandbox on the artifact's files. Exit 0 passes."""

    command: tuple[str, ...] = Field(min_length=1)
    timeout_s: float = 600.0
    kind: FindingKind = FindingKind.TEST_FAILURE  # the finding emitted when it fails


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


class ModelConfig(Strict):
    """A chat model from any provider LangChain supports, chosen by the user, e.g. "ollama:qwen2.5-coder:7b",
    "openai:gpt-5" or "anthropic:claude-sonnet-4-6". The provider's package must be installed as an extra.
    API keys come from the provider's usual environment variables, never from this file."""

    model: str = Field(pattern=r"^[A-Za-z0-9_\-]+:.+$")  # "<provider>:<model name>"
    temperature: float | None = None
    max_tokens: int | None = None
    base_url: str | None = None  # e.g. a remote Ollama server
    timeout_s: float | None = None

    @property
    def provider(self) -> str:
        return self.model.split(":", 1)[0]


class TeamConfig(Strict):
    use: str  # name in the team catalog
    watches: Level
    model: str | None = None  # name of an entry in ProjectConfig.models
    checks: tuple[str, ...] = ()  # checks the team may run while working, by name
    max_model_calls: int = Field(default=40, gt=0)  # per act; the agent stops when it is reached
    constraints: tuple[str, ...] = ()  # extra rules given to the model with every task


class Budgets(Strict):
    usd_per_day: float | None = None
    max_rounds_per_artifact: int | None = None


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
    jev: JevConfig = JevConfig()

    @field_validator("levels", mode="before")
    @classmethod
    def _ladder_from_list(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return {"levels": cast(Sequence[object], value)}
        return value

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        for team in self.teams:
            if not self.levels.contains(team.watches):
                raise ValueError(f"team {team.use!r} watches unknown level {team.watches!r}")
            if team.model is not None and team.model not in self.models:
                raise ValueError(f"team {team.use!r} uses undefined model {team.model!r}")
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
        return self

    def gate_from(self, level: Level) -> GateConfig | None:
        return next((g for g in self.gates if g.from_level == level), None)

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load a config file. A relative `seed` is resolved against the file's directory."""
        config = cls.model_validate(yaml.safe_load(path.read_text()))
        if config.seed is not None and not config.seed.is_absolute():
            config = config.model_copy(update={"seed": (path.parent / config.seed).resolve()})
        return config
