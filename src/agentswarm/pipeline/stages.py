"""The stages of a document run, as plain classes over the existing parts (the finder, the document project,
the citation audit, the page). The graph (graph.py) only orders them and pauses for the user; each stage here
has a Protocol so the graph can be tested with fakes."""

import asyncio
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict

from agentswarm.control.escalations import Escalations
from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig, SpendLimits
from agentswarm.core.domain import ArtifactId, Resolution, Usage
from agentswarm.evaluation.citations import CitationAudit, Support
from agentswarm.evaluation.documents import DocumentAssembly, DocumentImport
from agentswarm.evaluation.papers import Found, PaperFinder, WebPage
from agentswarm.llm.models import ChatModels
from agentswarm.llm.workers import LangChainWorkers, Worker
from agentswarm.pipeline.page import PageRenderer
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import AuditResult, Choice, DocumentRun
from agentswarm.project import Project, ProjectServices
from agentswarm.teams.catalog import TeamCatalog


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Written(Frozen):
    """What a writing (or review) stage left: the document, and the sections that failed their checks."""

    document: Path | None
    sections: int
    failed: tuple[str, ...]  # one line per failed section: its task and why


class Sources(Protocol):
    def find(self, run: DocumentRun, policy: Policy) -> str:
        """Fills run.sources and returns the reading list for the user to approve."""
        ...


class Writing(Protocol):
    def write(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written: ...

    def settle(self, run: DocumentRun, choice: Choice) -> None:
        """Applies the user's choice (retry, accept, drop) to every failed section."""
        ...


class Checking(Protocol):
    def audit(self, run: DocumentRun, policy: Policy) -> AuditResult: ...

    def review(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written: ...


class Publishing(Protocol):
    def publish(self, run: DocumentRun, policy: Policy) -> Path: ...


class Spending(Protocol):
    def spent(self, run: DocumentRun) -> float:
        """What the run has cost so far, every stage and every day together."""
        ...


class Names:
    """The names a run's spend is recorded under: its finder, and its document project."""

    @staticmethod
    def finder(run: DocumentRun) -> str:
        return f"run-{run.request.name}"

    @staticmethod
    def project(run: DocumentRun) -> str:
        return f"doc-{run.request.name}"


class MeteredFinder:
    """The finder's model, its spend recorded in the machine's ledger under the run's name."""

    MODEL = "planner"  # a strong model: the choice of sources shapes the whole document

    def __init__(self, record_as: str) -> None:
        self._record_as = record_as
        self.spent = 0.0

    def worker(self) -> Worker:
        models = DocumentImport.models()
        model = models[self.MODEL]
        limits = SpendLimits.load()
        shared = SharedSpend(limits, SystemClock()) if limits is not None else None

        def meter(usage: Usage) -> None:
            usd = model.usd_per_million.usd_of(usage) if model.usd_per_million is not None else 0.0
            self.spent += usd
            if shared is not None:
                shared.record(self._record_as, model.account, usd)

        return LangChainWorkers(ChatModels(models)).worker(self.MODEL, 1, meter)


class SourceStage:
    """The finder, or the user's own files: everything the document may draw on, in run.sources."""

    def find(self, run: DocumentRun, policy: Policy) -> str:
        request = run.request
        run.sources.mkdir(parents=True, exist_ok=True)
        if request.files:  # the user's own documents: given, nothing is searched
            for path in request.files:
                shutil.copy2(path.expanduser(), run.sources / path.name)
            return "your own files: " + ", ".join(p.name for p in request.files)
        finder = PaperFinder(MeteredFinder(Names.finder(run)).worker(), request.source.search())
        found = asyncio.run(finder.find(request.topic, request.reader, policy.papers, run.sources))
        named: list[Found] = []
        for url in request.urls:
            try:
                page, data = WebPage().record(url, date.today().year)
            except OSError:
                continue
            (run.sources / page.file).write_bytes(data)
            named.append(Found(paper=page, why="Named by the user as a starting source.", file=page.file))
        if named:
            found = found.model_copy(update={"papers": (*found.papers, *named)})
            found.save(run.sources)
        return found.render()


class ProjectStage:
    """The document project the sources become: written by the briefer and the section writers, reviewed and
    audited on the same board."""

    MAX_ROUNDS = 300
    REVIEW = "project-review.yaml"

    def __init__(self, spending: Spending, catalog: TeamCatalog | None = None) -> None:
        self._spending = spending
        self._catalog = catalog or TeamCatalog()

    def config(self, run: DocumentRun, policy: Policy, budget_left: float) -> Path:
        path = run.project / "project.yaml"
        if not path.is_file():
            DocumentImport.refresh(run.sources)  # papers fetched by hand find their records
            DocumentImport(
                DocumentImport.files([run.sources]),
                run.folder,
                run.request.goal or policy.brief(run.request.topic, run.request.reader),
                (),
                run.request.words or policy.words,
                (),  # reviews come later, by the policy
                False,  # the user approved the sources; the brief isn't another pause
                budget_left,
                "project",
                Names.project(run),
            ).write()
        return path

    def write(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written:
        return self.run(run, self.capped(self.config(run, policy, budget_left), run, budget_left))

    def review(self, run: DocumentRun, policy: Policy, budget_left: float) -> Written:
        config: dict[str, Any] = yaml.safe_load(self.config(run, policy, budget_left).read_text())
        config["review"] = {
            "model": "gpt-sol",
            "referee": "referee",
            "perspectives": ["facts"],
            "quiet_rounds": 1,
        }
        path = run.project / self.REVIEW
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        return self.run(run, self.capped(path, run, budget_left))

    def capped(self, path: Path, run: DocumentRun, budget_left: float) -> Path:
        """The project's own budget set to what the run has left: the project counts its spend per day, so
        its cap is what it spent today plus the rest of the run's budget."""
        config: dict[str, Any] = yaml.safe_load(path.read_text())
        today = self.today(run)
        config["budgets"] = {**config.get("budgets", {}), "usd_per_day": round(today + budget_left, 4)}
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        return path

    def today(self, run: DocumentRun) -> float:
        limits = SpendLimits.load()
        if limits is None:
            return 0.0
        shared = SharedSpend(limits, SystemClock())
        try:
            return shared.spent_today_by(Names.project(run))
        finally:
            shared.close()

    def run(self, run: DocumentRun, path: Path) -> Written:
        config = ProjectConfig.load(path)
        project = Project.open(config, run.board, ProjectServices(teams=self._catalog))
        try:
            asyncio.run(project.run_until_idle(self.MAX_ROUNDS))
            level = run.board / "levels" / config.levels.base
            document = DocumentAssembly.write(level, run.board / "document.md")
            failed = tuple(
                f"{e.artifact}: {e.reason.splitlines()[-1]}" for e in Escalations(project.board, None).open()
            )
            sections = len(list((level / "sections").glob("*.md")))
        finally:
            project.close()
        return Written(document=document, sections=sections, failed=failed)

    def settle(self, run: DocumentRun, choice: Choice) -> None:
        config = ProjectConfig.load(run.project / "project.yaml")
        project = Project.open(config, run.board, ProjectServices(teams=self._catalog))
        try:
            queue = Escalations(project.board, None)
            for escalation in queue.open():
                queue.resolve(
                    ArtifactId(escalation.artifact), Resolution(choice.value), "the user's choice in the run"
                )
        finally:
            project.close()

    def audit(self, run: DocumentRun, policy: Policy) -> AuditResult:
        config = ProjectConfig.load(run.project / "project.yaml")
        project = Project.open(config, run.board, ProjectServices(teams=self._catalog))
        try:
            referee = config.review.referee if config.review is not None else "referee"
            audit = CitationAudit(
                run.board / "levels" / config.levels.base, project.worker(referee, "citation-audit")
            )
            results = asyncio.run(
                audit.audit(audit.sample(policy.audit_sample, policy.audit_seed + run.reviews))
            )
        finally:
            project.close()
        return AuditResult(
            supported=sum(r.support is Support.SUPPORTED for r in results),
            partly=sum(r.support is Support.PARTLY for r in results),
            unsupported=sum(r.support is Support.UNSUPPORTED for r in results),
            unruled=sum(r.support is None for r in results),
        )


class LedgerSpending:
    """The run's cost from the machine's ledger: its finder and its document project, every day."""

    def spent(self, run: DocumentRun) -> float:
        limits = SpendLimits.load()
        if limits is None:
            return 0.0
        shared = SharedSpend(limits, SystemClock())
        try:
            return shared.spent_by([Names.finder(run), Names.project(run)])
        finally:
            shared.close()


class PageStage:
    """The document as a page for the site: written into the homepage repository's static folder and listed
    in its tutorials.json (src/web/tutorials.py there reads it), or beside the run without a homepage."""

    STATIC = Path("src/web/static/agentswarm")
    LISTING = Path("src/web/tutorials.json")

    def __init__(self, renderer: PageRenderer | None = None) -> None:
        self._renderer = renderer or PageRenderer()

    def publish(self, run: DocumentRun, policy: Policy) -> Path:
        assert run.document is not None
        note = self.note(run)
        if policy.homepage is None:
            return self._renderer.write(run.document, run.folder / "page.html", note)
        page = self._renderer.write(
            run.document, policy.homepage / self.STATIC / f"{run.request.name}.html", note
        )
        listing = policy.homepage / self.LISTING
        entries: list[dict[str, str]] = json.loads(listing.read_text()) if listing.is_file() else []
        href = f"/static/agentswarm/{run.request.name}.html"
        entries = [e for e in entries if e.get("href") != href]
        entries.insert(
            0,
            {
                "href": href,
                "title": self._renderer.title(run.document),
                "date": date.today().isoformat(),
                "summary": run.request.topic,
            },
        )
        listing.write_text(json.dumps(entries, indent=1) + "\n")
        return page

    @staticmethod
    def note(run: DocumentRun) -> str:
        audit = run.audits[-1] if run.audits else None
        rounds = f"{run.reviews} round{'s' if run.reviews != 1 else ''}"
        checked = (
            f" After {rounds} of fact review, a sampled audit found {audit.share:.0%} of cited statements "
            "fully supported by their source."
            if audit is not None
            else " Its citations were not audited."
        )
        return (
            f"Written by AgentSwarm on {date.today():%d %B %Y} for {run.request.reader}, from sources it "
            f"found and the user approved (listed under References).{checked} Not reviewed by an expert: "
            "read it as a guide to its sources, not a substitute for them. "
            '<a href="/work/agentswarm">About AgentSwarm</a>.'
        )


class Stages:
    """Every stage a run needs, injected into the graph."""

    def __init__(
        self,
        sources: Sources,
        writing: Writing,
        checking: Checking,
        publishing: Publishing,
        spending: Spending,
    ) -> None:
        self.sources = sources
        self.writing = writing
        self.checking = checking
        self.publishing = publishing
        self.spending = spending

    @classmethod
    def real(cls) -> "Stages":
        spending = LedgerSpending()
        project = ProjectStage(spending)
        return cls(SourceStage(), project, project, PageStage(), spending)
