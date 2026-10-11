"""The web monitor's server: a read-only page over a project's board, on localhost (`agentswarm monitor`).

Python's own HTTP server, no framework: three routes, every one answered from a fresh read of the log, so the
page shows a running project as it moves and can never change it.

    /                    the page (page.html), which asks for the rest every few seconds
    /api/state           `State`: overview, teams, models and the plan as a tree
    /api/artifact/<id>   `Detail`: one step's history
"""

from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import ArtifactId, ProjectId
from agentswarm.monitor.state import MonitorState


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: ProjectConfig, board: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.config = config
        self.board = board  # the project's blackboard.sqlite
        super().__init__((host, port), MonitorHandler)

    def read[T](self, view: Callable[[MonitorState], T]) -> T:
        with SqliteEventLog(self.board, ProjectId(self.config.project), SystemClock()) as log:
            return view(MonitorState(self.config, log))

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host!s}:{port}/"


class MonitorHandler(BaseHTTPRequestHandler):
    PAGE: ClassVar[str] = "page.html"
    ARTIFACT: ClassVar[str] = "/api/artifact/"

    @property
    def monitor(self) -> MonitorServer:
        assert isinstance(self.server, MonitorServer)
        return self.server

    def do_GET(self) -> None:  # the name http.server calls
        path = self.path.split("?", 1)[0]
        if path == "/":
            page = resources.files("agentswarm.monitor").joinpath(self.PAGE).read_text()
            self.send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self.json(self.monitor.read(lambda m: m.state()))
        elif path.startswith(self.ARTIFACT):
            detail = self.monitor.read(lambda m: m.detail(ArtifactId(path.removeprefix(self.ARTIFACT))))
            if detail is None:
                self.send(HTTPStatus.NOT_FOUND, b"no such artifact", "text/plain")
            else:
                self.json(detail)
        else:
            self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

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
