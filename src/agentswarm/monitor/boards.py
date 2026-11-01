"""The board view's data: read-only answers over projects' boards, served by the studio (studio/app.py) under

    /board                                   the page (page.html), which asks for the rest every few seconds
    /api/projects                            a `Summary` per project: the list of all projects
    /api/projects/<project>/state            `State`: overview, teams, models and the plan as a tree
    /api/projects/<project>/artifact/<id>    `Detail`: one step's history

Every answer comes from a fresh read of a project's log, so the page shows running projects as they move and
can never change them.
"""

from collections.abc import Callable, Mapping
from importlib import resources
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, RootModel

from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import ArtifactId, ProjectId
from agentswarm.monitor.state import Detail, MonitorState, State, Summary


class Watched(BaseModel):
    """A project the board view shows: its configuration and its board."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    config: ProjectConfig
    board: Path  # the project's blackboard.sqlite, which may not exist yet


class Summaries(RootModel[tuple[Summary, ...]]):
    pass


class Boards:
    """The projects the board view shows, asked for afresh on every read: document runs come and go."""

    PAGE: ClassVar[str] = "page.html"

    def __init__(self, projects: Callable[[], Mapping[str, Watched]]) -> None:
        self._projects = projects

    def read[T](self, project: str, view: Callable[[MonitorState], T]) -> T | None:
        watched = self._projects().get(project)
        if watched is None or not watched.board.is_file():
            return None
        with SqliteEventLog(watched.board, ProjectId(watched.config.project), SystemClock()) as log:
            return view(MonitorState(watched.config, log))

    def summaries(self) -> Summaries:
        rows: list[Summary] = []
        for name, watched in self._projects().items():
            summary = self.read(name, lambda m: m.summary())
            rows.append(summary or Summary(project=name, north_star=watched.config.north_star, started=False))
        return Summaries(tuple(rows))

    def state(self, project: str) -> State | None:
        return self.read(project, lambda m: m.state())

    def detail(self, project: str, artifact: str) -> Detail | None:
        return self.read(project, lambda m: m.detail(ArtifactId(artifact)))

    def page(self) -> str:
        return resources.files("agentswarm.monitor").joinpath(self.PAGE).read_text()
