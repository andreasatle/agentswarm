"""A swarm: several projects in one process, sharing one scheduler (proposal §14.2, Phase 6).

    scheduler: {max_model_calls: 4, jev_per_minute: 1200, max_sandboxes: 4}
    projects:
      - {config: greeter/project.yaml, root: ~/agentswarm-runs/greeter}
      - {config: handbook/project.yaml, root: ~/agentswarm-runs/handbook}

Each project keeps its own board, decision log, repository and budget (§16, isolation); they share only
the scheduler's resources. Each project's loop is its own asyncio task; their blocking work runs in
threads (see Project.step), so the projects really overlap.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentswarm.control.scheduler import Scheduler, SchedulerConfig
from agentswarm.core.config import ProjectConfig
from agentswarm.project import Project, ProjectServices, StillBusy


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SwarmProject(Frozen):
    config: Path
    root: Path


class SwarmConfig(Frozen):
    scheduler: SchedulerConfig = SchedulerConfig()
    projects: tuple[SwarmProject, ...] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> Self:
        """Relative paths are resolved against the swarm file; `~` is expanded."""
        swarm = cls.model_validate(yaml.safe_load(path.read_text()))
        base = path.parent.resolve()
        projects = tuple(
            SwarmProject(config=(base / p.config).resolve(), root=(base / p.root.expanduser()).resolve())
            for p in swarm.projects
        )
        return swarm.model_copy(update={"projects": projects})


class ProjectRun(Frozen):
    project: str
    rounds: int | None  # rounds that took action, or None if it stopped early
    error: str = ""  # why it stopped early
    failed: bool = False  # it raised an error, as opposed to still being busy at the round limit

    @property
    def status(self) -> str:
        if self.failed:
            return f"FAILED: {self.error}"
        return f"{self.rounds} rounds" if self.rounds is not None else f"still busy: {self.error}"


class Swarm:
    def __init__(self, projects: list[Project], scheduler: Scheduler) -> None:
        names = [p.config.project for p in projects]
        if len(set(names)) != len(names):
            raise ValueError(f"project names must be unique in a swarm: {names}")
        self.projects = {p.config.project: p for p in projects}
        self.scheduler = scheduler

    @classmethod
    def open(
        cls,
        config: SwarmConfig,
        services_for: Callable[[ProjectConfig], ProjectServices] | None = None,  # tests inject fakes
    ) -> Self:
        scheduler = Scheduler(config.scheduler)
        configs = [(ProjectConfig.load(entry.config), entry.root) for entry in config.projects]
        cls._check_distinct(configs)  # before opening anything
        projects: list[Project] = []
        try:
            for project_config, root in configs:
                base = services_for(project_config) if services_for is not None else ProjectServices()
                projects.append(
                    Project.open(project_config, root, dataclasses.replace(base, scheduler=scheduler))
                )
        except BaseException:
            for project in projects:
                project.close()
            raise
        return cls(projects, scheduler)

    @staticmethod
    def _check_distinct(configs: list[tuple[ProjectConfig, Path]]) -> None:
        names = [c.project for c, _ in configs]
        if len(set(names)) != len(names):
            raise ValueError(f"project names must be unique in a swarm: {names}")
        roots = [root.expanduser().resolve() for _, root in configs]
        if len(set(roots)) != len(roots):
            raise ValueError(f"project roots must be distinct in a swarm: {[str(r) for r in roots]}")

    async def run(self, max_rounds: int = 50) -> list[ProjectRun]:
        """Run every project until it is idle, concurrently. A project that fails is reported, not raised:
        every project has finished when this returns."""
        return list(
            await asyncio.gather(*(self._run(name, p, max_rounds) for name, p in self.projects.items()))
        )

    @staticmethod
    async def _run(name: str, project: Project, max_rounds: int) -> ProjectRun:
        try:
            return ProjectRun(project=name, rounds=await project.run_until_idle(max_rounds))
        except StillBusy as error:  # report it; the others carry on
            return ProjectRun(project=name, rounds=None, error=str(error))
        except (
            Exception
        ) as error:  # one project's failure never stops or closes the others (CODEX_AUDIT.md F06)
            logging.getLogger(__name__).exception("project %s failed", name)
            return ProjectRun(
                project=name, rounds=None, error=f"{type(error).__name__}: {error}", failed=True
            )

    def close(self) -> None:
        for project in self.projects.values():
            project.close()
