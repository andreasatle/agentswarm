"""The web monitor's server: a read-only page over projects' boards, on localhost (`agentswarm monitor`).

Python's own HTTP server, no framework; every answer comes from a fresh read of a project's log, so the page
shows running projects as they move and can never change them.

    /                                        the page (page.html), which asks for the rest every few seconds
    /api/projects                            a `Summary` per project: the list of all projects
    /api/projects/<project>/state            `State`: overview, teams, models and the plan as a tree
    /api/projects/<project>/artifact/<id>    `Detail`: one step's history
"""

from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, RootModel

from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import ArtifactId, ProjectId
from agentswarm.monitor.state import MonitorState, Summary


class Watched(BaseModel):
    """A project the monitor shows: its configuration and its board."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    config: ProjectConfig
    board: Path  # the project's blackboard.sqlite, which may not exist yet


class Summaries(RootModel[tuple[Summary, ...]]):
    pass


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, projects: Mapping[str, Watched], host: str = "127.0.0.1", port: int = 8765) -> None:
        if not projects:
            raise ValueError("nothing to monitor")
        self.projects = dict(projects)
        super().__init__((host, port), MonitorHandler)

    def read[T](self, project: str, view: Callable[[MonitorState], T]) -> T | None:
        watched = self.projects.get(project)
        if watched is None or not watched.board.is_file():
            return None
        with SqliteEventLog(watched.board, ProjectId(watched.config.project), SystemClock()) as log:
            return view(MonitorState(watched.config, log))

    def summaries(self) -> Summaries:
        rows: list[Summary] = []
        for name, watched in self.projects.items():
            summary = self.read(name, lambda m: m.summary())
            rows.append(summary or Summary(project=name, north_star=watched.config.north_star, started=False))
        return Summaries(tuple(rows))

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host!s}:{port}/"


class MonitorHandler(BaseHTTPRequestHandler):
    PAGE: ClassVar[str] = "page.html"
    API: ClassVar[str] = "/api/projects"

    @property
    def monitor(self) -> MonitorServer:
        assert isinstance(self.server, MonitorServer)
        return self.server

    def do_GET(self) -> None:  # the name http.server calls
        path = unquote(self.path.split("?", 1)[0])
        if path == "/":
            page = resources.files("agentswarm.monitor").joinpath(self.PAGE).read_text()
            self.send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
            return
        if path == self.API:
            self.json(self.monitor.summaries())
            return
        parts = path.removeprefix(self.API + "/").split("/") if path.startswith(self.API + "/") else []
        answer: BaseModel | None = None
        match parts:
            case [project, "state"]:
                answer = self.monitor.read(project, lambda m: m.state())
            case [project, "artifact", artifact]:
                answer = self.monitor.read(project, lambda m: m.detail(ArtifactId(artifact)))
            case _:
                pass
        if answer is None:
            self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        else:
            self.json(answer)

    def json(self, model: BaseModel) -> None:
        self.send(HTTPStatus.OK, model.model_dump_json().encode(), "application/json")

    def send(self, status: HTTPStatus, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass  # quiet: the page asks every few seconds
