"""The studio's web app (docs/design/studio.md): document runs started, followed and answered in a browser,
and the board view beside them. It calls the same DocumentPipeline as the commands; steps that take minutes
run on a Worker, so every page answers at once."""

import html
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar

import markdown
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError

from agentswarm.core.config import ProjectConfig
from agentswarm.evaluation.papers import PaperSource
from agentswarm.monitor.boards import Boards, Watched
from agentswarm.monitor.state import Summary
from agentswarm.pipeline.graph import DocumentPipeline
from agentswarm.pipeline.page import PageRenderer
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import Answer, Choice, RunRequest, RunView, Stage
from agentswarm.pipeline.stages import Names, PageStage
from agentswarm.studio.worker import Worker


class Studio:
    HOST: ClassVar[str] = "127.0.0.1"  # this Mac only: there is no login (the user, 2026-11-01)
    PORT: ClassVar[int] = 8770
    HERE: ClassVar[Path] = Path(__file__).parent
    UPLOADS: ClassVar[str] = "uploads"  # the user's own files for a new run, beside it
    BUDGET: ClassVar[str] = "1.50"  # the form's first suggestion

    CHOICES: ClassVar[dict[Choice, str]] = {
        Choice.YES: "Approve",
        Choice.BUDGET: "Raise the budget",
        Choice.RETRY: "Try again",
        Choice.ACCEPT: "Accept as they are",
        Choice.DROP: "Leave them out",
        Choice.STOP: "Stop the run",
    }
    STAGES: ClassVar[dict[Stage, str]] = {
        Stage.STARTED: "starting",
        Stage.SOURCES: "sources",
        Stage.WRITING: "writing",
        Stage.CHECKING: "checking",
        Stage.PUBLISHING: "ready to publish",
        Stage.PUBLISHED: "page written",
        Stage.STOPPED: "stopped",
    }

    def __init__(
        self,
        pipeline: DocumentPipeline,
        policy: Policy,
        runs: Path,
        worker: Worker,
        projects: Mapping[str, Watched] | None = None,  # boards to show beside the document runs'
        renderer: PageRenderer | None = None,
        port: int = PORT,
    ) -> None:
        self.port = port
        self._pipeline = pipeline
        self._policy = policy
        self._runs = runs
        self._worker = worker
        self._projects = dict(projects or {})
        self._renderer = renderer or PageRenderer()
        self._notices: dict[str, str] = {}  # a run's message for the next time its page is shown
        self.boards = Boards(self.watched)
        self._templates = Jinja2Templates(directory=self.HERE / "templates")
        self._templates.env.filters["prose"] = self.prose
        self._templates.env.filters["usd"] = self.usd
        self.app = self.build()

    @property
    def url(self) -> str:
        return f"http://{self.HOST}:{self.port}/"

    def serve(self) -> None:
        """Until interrupted (Ctrl-C)."""
        uvicorn.run(self.app, host=self.HOST, port=self.port, log_level="warning")

    def build(self) -> FastAPI:
        app = FastAPI(title="AgentSwarm studio", docs_url=None, redoc_url=None, openapi_url=None)
        app.mount("/static", StaticFiles(directory=self.HERE / "static"), name="static")
        page: dict[str, Any] = {"response_class": HTMLResponse, "response_model": None}
        app.add_api_route("/", self.index, methods=["GET"], **page)
        app.add_api_route("/runs", self.runs_list, methods=["GET"], **page)
        app.add_api_route("/new", self.new, methods=["GET"], **page)
        app.add_api_route("/new", self.create, methods=["POST"], **page)
        app.add_api_route("/plan", self.plan, methods=["GET"], **page)
        app.add_api_route("/runs/{name}", self.run, methods=["GET"], **page)
        app.add_api_route("/runs/{name}/panel", self.panel, methods=["GET"], **page)
        app.add_api_route("/runs/{name}/answer", self.answer, methods=["POST"], **page)
        app.add_api_route("/runs/{name}/files", self.files, methods=["POST"], **page)
        app.add_api_route("/runs/{name}/document", self.document, methods=["GET"], **page)
        app.add_api_route("/board", self.board, methods=["GET"], **page)
        app.add_api_route("/api/projects", self.projects, methods=["GET"], response_model=None)
        app.add_api_route("/api/projects/{project}/state", self.state, methods=["GET"], response_model=None)
        app.add_api_route(
            "/api/projects/{project}/artifact/{artifact}", self.detail, methods=["GET"], response_model=None
        )
        return app

    # Pages

    def index(self, request: Request) -> Response:
        return self.render(request, "index.html", rows=self.rows())

    def runs_list(self, request: Request) -> Response:
        return self.render(request, "_runs.html", rows=self.rows())

    def new(self, request: Request) -> Response:
        return self.form(request, {}, None)

    def create(
        self,
        request: Request,
        topic: Annotated[str, Form()],
        reader: Annotated[str, Form()],
        budget: Annotated[str, Form()],
        source: Annotated[str, Form()] = PaperSource.OPENALEX.value,
        words: Annotated[str, Form()] = "",
        name: Annotated[str, Form()] = "",
        urls: Annotated[str, Form()] = "",
        goal: Annotated[str, Form()] = "",
        files: Annotated[list[UploadFile] | None, File()] = None,
    ) -> Response:
        values = {
            "topic": topic,
            "reader": reader,
            "budget": budget,
            "source": source,
            "words": words,
            "name": name,
            "urls": urls,
            "goal": goal,
        }
        uploads = [f for f in files or [] if f.filename]
        chosen = name.strip() or RunRequest.slug(topic)
        try:
            request_ = RunRequest(
                name=chosen,
                topic=topic.strip(),
                reader=reader.strip(),
                budget=float(budget or 0),
                source=PaperSource(source),
                words=int(words) if words.strip() else None,
                urls=tuple(u.strip() for u in urls.splitlines() if u.strip()),
                goal=goal.strip() or None,
            )
        except (ValidationError, ValueError) as error:
            return self.form(request, values, self.problem(error))
        if self._pipeline.exists(chosen):
            return self.form(request, values, f"a run named {chosen} exists already: choose another name")
        folder = self._runs / chosen
        if uploads:
            kept = [self.save(f, folder / self.UPLOADS) for f in uploads]
            request_ = request_.model_copy(update={"files": tuple(kept)})
        self._worker.submit(chosen, "finding sources", lambda: self._pipeline.start(request_, folder))
        return RedirectResponse(f"/runs/{chosen}", status_code=303)

    def plan(self, request: Request, words: str = "", budget: str = "") -> Response:
        count = int(words) if words.isdigit() and int(words) > 0 else self._policy.words
        limit = float(budget) if self.number(budget) else None
        return self.render(
            request,
            "_plan.html",
            steps=self.steps(count),
            expected=self._policy.expected(count),
            budget=limit,
            words=count,
        )

    def run(self, request: Request, name: str) -> Response:
        if not self._pipeline.exists(name) and self._worker.busy(name) is None:
            return self.missing(request, name)
        return self.render(request, "run.html", **self.context(name))

    def panel(self, request: Request, name: str) -> Response:
        if not self._pipeline.exists(name) and self._worker.busy(name) is None:
            return self.missing(request, name)
        return self.render(request, "_panel.html", **self.context(name))

    def answer(
        self,
        name: str,
        choice: Annotated[str, Form()],
        budget: Annotated[str, Form()] = "",
    ) -> Response:
        try:
            given = Answer(choice=Choice(choice), budget=float(budget) if self.number(budget) else None)
            self._pipeline.check(name, given)
            label = self.label(self._pipeline.view(name), given)
            self._worker.submit(name, label, lambda: self._pipeline.answer(name, given))
        except (ValueError, ValidationError) as error:
            self._notices[name] = self.problem(error)
        return RedirectResponse(f"/runs/{name}", status_code=303)

    def files(self, name: str, files: Annotated[list[UploadFile], File()]) -> Response:
        """Papers fetched by hand, into the run's sources folder, under the names they came with."""
        view = self._pipeline.view(name)
        if view.run.stage is not Stage.SOURCES or view.question is None:
            self._notices[name] = "files can be added only while the run waits for its sources"
        else:
            kept = [self.save(f, view.run.sources).name for f in files if f.filename]
            self._notices[name] = f"added {', '.join(kept)}" if kept else "no file was chosen"
        return RedirectResponse(f"/runs/{name}", status_code=303)

    def document(self, request: Request, name: str) -> Response:
        run = self._pipeline.run(name)
        if run.document is None or not run.document.is_file():
            return self.missing(request, name)
        return HTMLResponse(self._renderer.render(run.document.read_text(), PageStage.note(run)))

    def board(self) -> Response:
        return HTMLResponse(self.boards.page())

    # The board's data (read only)

    def projects(self) -> Response:
        return Response(self.boards.summaries().model_dump_json(), media_type="application/json")

    def state(self, project: str) -> Response:
        return self.json(self.boards.state(project))

    def detail(self, project: str, artifact: str) -> Response:
        return self.json(self.boards.detail(project, artifact))

    def watched(self) -> dict[str, Watched]:
        """The boards shown: the projects given, and every document run's once it has a configuration."""
        boards = dict(self._projects)
        for name in self._pipeline.runs():
            run = self._pipeline.run(name)
            config = run.project / "project.yaml"
            if config.is_file():
                loaded = ProjectConfig.load(config)
                boards[loaded.project] = Watched(config=loaded, board=run.board / "blackboard.sqlite")
        return boards

    # Helpers

    def rows(self) -> list[dict[str, Any]]:
        names = self._pipeline.runs()
        return [self.context(n) for n in reversed(names)]  # the newest first

    def context(self, name: str) -> dict[str, Any]:
        busy = self._worker.busy(name)
        view = self._pipeline.view(name) if self._pipeline.exists(name) else None
        error = self._worker.error(name)
        if view is not None and view.failure is not None:
            error = view.failure
        board = None
        if view is not None:
            board = self.boards.read(Names.project(view.run), lambda m: m.summary())
        return {
            "name": name,
            "view": view,
            "busy": busy,
            "error": None if busy else error,
            "board": board,
            "status": self.status(view, busy, error),
            "notice": self._notices.pop(name, None),
            "sourcing": view is not None and view.run.stage is Stage.SOURCES and view.question is not None,
            "share": self.share(view),
        }

    def form(self, request: Request, values: dict[str, str], error: str | None) -> Response:
        words = int(values["words"]) if values.get("words", "").isdigit() else self._policy.words
        given = values.get("budget") or self.BUDGET
        return self.render(
            request,
            "new.html",
            values=values,
            error=error,
            sources=list(PaperSource),
            default_words=self._policy.words,
            default_budget=self.BUDGET,
            steps=self.steps(words),
            expected=self._policy.expected(words),
            budget=float(given) if self.number(given) else None,
            words=words,
            status_code=400 if error else 200,
        )

    def render(self, request: Request, template: str, status_code: int = 200, **context: Any) -> Response:
        """Every page gets the labels: the runs list shows a stage too, not only a run's own page."""
        labels = {"stages": self.STAGES, "choices": self.CHOICES}
        return self._templates.TemplateResponse(request, template, labels | context, status_code=status_code)

    def missing(self, request: Request, name: str) -> Response:
        return self.render(request, "missing.html", name=name, status_code=404)

    def steps(self, words: int) -> list[tuple[str, float]]:
        e = self._policy.estimates
        steps = [("Find sources", e.find), ("Write", e.write(words)), ("Audit the citations", e.audit)]
        if self._policy.review_rounds:
            steps.append(("Fact review, if needed, and a new audit", e.review(10) + e.audit))
        return steps

    def label(self, view: RunView, answer: Answer) -> str:
        if answer.choice is Choice.STOP:
            return "stopping"
        if answer.choice is Choice.RETRY and view.failure is not None:
            return f"retrying {view.failure.partition(':')[0]}"
        if view.run.stage is Stage.SOURCES:
            return "writing"
        if view.run.stage is Stage.PUBLISHING:
            return "making the page"
        if answer.choice is Choice.BUDGET and view.run.blocked is not None:
            return {
                "find": "finding sources",
                "write": "writing",
                "audit": "auditing",
                "review": "reviewing",
            }[view.run.blocked]
        return "working"

    @staticmethod
    def status(view: RunView | None, busy: str | None, error: str | None) -> str:
        if busy is not None:
            return "working"
        if error is not None:
            return "error"
        if view is None:
            return "working"
        if view.question is not None:
            return "waiting"
        return {Stage.PUBLISHED: "done", Stage.STOPPED: "stopped"}.get(view.run.stage, "working")

    @staticmethod
    def share(view: RunView | None) -> float:
        if view is None or view.run.request.budget <= 0:
            return 0.0
        return min(view.spent / view.run.request.budget, 1.0)

    @staticmethod
    def save(upload: UploadFile, folder: Path) -> Path:
        """Under the name it came with, the folder part dropped: never a made-up name."""
        name = Path(upload.filename or "").name
        if not name or name.startswith("."):
            raise ValueError(f"not a file name: {upload.filename!r}")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(upload.file.read())
        return path

    @staticmethod
    def number(text: str) -> bool:
        try:
            return float(text) > 0
        except ValueError:
            return False

    @staticmethod
    def problem(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'value'}: {e['msg']}" for e in error.errors())
        return str(error)

    @staticmethod
    def prose(text: str) -> Markup:
        """A question's details (the reading list is Markdown) as HTML; titles from a search are data, so any
        HTML in the text is escaped first."""
        return Markup(markdown.markdown(html.escape(text, quote=False)))

    @staticmethod
    def usd(value: float) -> str:
        return f"${value:.2f}"

    @staticmethod
    def json(model: Summary | Any | None) -> Response:
        if model is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return Response(model.model_dump_json(), media_type="application/json")
