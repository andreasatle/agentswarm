"""Command line: run a project on a task and show what happened to its artifacts."""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.control.escalations import Escalations
from agentswarm.control.limits import SharedSpend
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import (
    JevConfig,
    ModelOverride,
    ProjectConfig,
    RoutingConfig,
    SandboxConfig,
    SpendLimits,
)
from agentswarm.core.domain import (
    ArtifactId,
    ProjectId,
    Resolution,
    Scored,
)
from agentswarm.evaluation.citations import CitationAudit
from agentswarm.evaluation.commonmark import CommonMarkImport
from agentswarm.evaluation.compare import (
    ComparisonFiles,
    ComparisonReport,
    ComparisonRun,
    Experiment,
    ExperimentKind,
)
from agentswarm.evaluation.difficulty import DifficultyRater, DifficultyRatings
from agentswarm.evaluation.documents import DocumentAssembly, DocumentImport, SourceText
from agentswarm.evaluation.faults import Variant
from agentswarm.evaluation.html5 import Html5Import
from agentswarm.evaluation.imports import EvalPlusFormat, EvalPlusReader, ImportCheck, SuiteWriter
from agentswarm.evaluation.jq import JqImport
from agentswarm.evaluation.jsonpath import JsonPathImport
from agentswarm.evaluation.probe import ProbeExperiment, ProbeFiles, ProbeReport, ProbeRun
from agentswarm.evaluation.qasper import GuideImport, QasperSet
from agentswarm.evaluation.questions import HiddenQuestions, QuestionTrace
from agentswarm.evaluation.review import ReviewReport, ReviewRun
from agentswarm.evaluation.run import EvaluationRun, TaskResult
from agentswarm.evaluation.score import Progress, Scoreboard
from agentswarm.evaluation.suite import EvalTask, TaskSuite
from agentswarm.evaluation.toml import TomlImport
from agentswarm.evaluation.yamltest import YamlImport
from agentswarm.jev.client import JevClient
from agentswarm.judging.audit import GateAudit
from agentswarm.judging.calibration import CalibrationReport
from agentswarm.judging.deciders import Decider, JevDecider
from agentswarm.judging.decisions import SqliteDecisions
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.judging.labelling import SpotCheck
from agentswarm.judging.tuning import ThresholdTuner, TuningReport
from agentswarm.llm.catalog import OpenRouterCatalog
from agentswarm.llm.workers import WorkerProvider
from agentswarm.monitor.report import HardeningSummary, Report, RunVerdict
from agentswarm.monitor.server import MonitorServer, Watched
from agentswarm.project import Project, ProjectServices
from agentswarm.prompts import PromptCatalogue
from agentswarm.swarm import Swarm, SwarmConfig
from agentswarm.teams.catalog import TeamCatalog
from agentswarm.teams.routing import Beliefs, Prior, RoutingLedger
from agentswarm.workspace.sandbox import Sandboxes
from agentswarm.workspace.view import LevelFolders


class Cli:
    """The `agentswarm` command line.

        config CONFIG                              the effective configuration, with the domain preset applied
        run CONFIG --root DIR [--task TEXT]...     add tasks and run the project until idle
        monitor [CONFIG --root DIR] [--project CONFIG=ROOT]... [--swarm FILE] [--port N]
                                                   a read-only web page of one or more projects, on localhost
        status CONFIG --root DIR                   every artifact's history
        evaluate SUITE --workdir DIR [--active P]  run a task suite; record outcomes; print calibration
        routing LEDGER                             what the router has learned: outcomes per model and band
        probe EXPERIMENT --workdir DIR             one prompt, one answer: models scored on single calls
        import-evalplus FILE --format F --out DIR  an EvalPlus benchmark as a task suite, references checked
        import-commonmark SPEC --out DIR           the CommonMark spec as a seed project and held-out tests
        import-toml SUITE --out DIR [--hide-all]   the toml-test suite (TOML 1.0.0) as a seed, some held out
        import-jq SOURCE --out DIR                 jq's own tests as a seed for an interpreter, some held out
        import-yaml SUITE --out DIR [--spec MD]    the yaml-test-suite (YAML 1.2) as a seed, some held out
                                [--hide-all]       ... or every test held out (no labels)
        import-html5 DIR --out DIR [--spec MD]     html5lib's tree-construction tests as a seed, some held out
        import-jsonpath SUITE --rfc TXT --out DIR  the JSONPath compliance suite (RFC 9535) as a seed
        import-guide QASPER --out DIR --topic TEXT --papers ID...
                                                   a guide to Qasper papers as a seed, questions held out
        qasper-papers QASPER WORD...               Qasper papers whose title or abstract holds every word
        import-docs FILE... --out DIR --goal TEXT [--audience TEXT]...
                                                   your documents as sources, a project per audience
        questions CONFIG --root DIR                a document's hidden questions, score by score
        score CONFIG --root DIR [--extra DIR]...   how far the first shared level is through a test suite
        openrouter-models ID...                    model entries for OpenRouter models, with today's prices
        spend                                      today's spend of every run, per account, against the limits
        rate SUITE... --out FILE                   Jev rates every task's difficulty, resumable
        prompts                                    every model call and Jev question, as the model sees it
        compare EXPERIMENT --workdir DIR           run a suite's tasks under several setups (build), or plant
                                                   bugs for their reviewers (review); compare them
        audit DECISIONS [--point POINT]            how often an active gate was wrong, against outcomes
        calibrate DECISIONS                        the calibration report for a decision log
        tune DECISIONS [--precision P]             thresholds that reach a target precision, per question
        label DECISIONS [--limit N]                spot-check automatic outcomes by hand
        swarm SWARM [--task PROJECT=TEXT]...       several projects at once, sharing one scheduler
        escalations CONFIG --root DIR              open escalations, with the decisions behind them
        resolve CONFIG --root DIR ARTIFACT --retry|--accept|--drop [--note TEXT]
        drop CONFIG --root DIR ARTIFACT            retire a task and every step split from it
        resume CONFIG --root DIR [--note TEXT]     lift a drift pause

    A `.env` file in the working directory is loaded first (e.g. TYPESAFE_API_KEY for Jev).
    """

    def __init__(
        self,
        catalog: TeamCatalog | None = None,
        show: Callable[[str], None] = print,
        ask: Callable[[str], str] = input,
        evaluation_services: Callable[[EvalTask], ProjectServices] | None = None,  # None: real models, Jev
        probe_workers: WorkerProvider | None = None,  # None: the probe experiment's real models
        rating_decider: Decider | None = None,  # None: Jev
    ) -> None:
        self._rating_decider = rating_decider
        self._probe_workers = probe_workers
        self._catalog = catalog or TeamCatalog.standard()
        self._show = show
        self._ask = ask
        self._evaluation_services = evaluation_services

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> int:
        load_dotenv(Path.cwd() / ".env")  # never overrides variables already set
        override = ModelOverride.path()
        if override is not None:  # said loudly: it changes every project's models
            print(
                f"{ModelOverride.VARIABLE}={override}: every model is replaced by this one", file=sys.stderr
            )
        return cls().dispatch(sys.argv[1:] if argv is None else argv)

    def dispatch(self, argv: Sequence[str]) -> int:
        args = self.parser().parse_args(argv)
        match args.command:
            case "config":
                return self.config(args)
            case "run":
                return self.run(args)
            case "status":
                return self.status(args)
            case "evaluate":
                return asyncio.run(self.evaluate(args))
            case "compare":
                return asyncio.run(self.compare(args))
            case "routing":
                return self.routing(args)
            case "probe":
                return asyncio.run(self.probe(args))
            case "import-evalplus":
                return self.import_evalplus(args)
            case "import-commonmark":
                return self.import_commonmark(args)
            case "monitor":
                return self.monitor(args)
            case "import-toml":
                return self.import_toml(args)
            case "import-jq":
                return self.import_jq(args)
            case "import-yaml":
                return self.import_yaml(args)
            case "import-html5":
                return self.import_html5(args)
            case "import-jsonpath":
                return self.import_jsonpath(args)
            case "import-guide":
                return self.import_guide(args)
            case "qasper-papers":
                return self.qasper_papers(args)
            case "import-docs":
                return self.import_docs(args)
            case "learn":
                return self.learn(args)
            case "audit-citations":
                return self.audit_citations(args)
            case "questions":
                return self.questions(args)
            case "score":
                return self.score(args)
            case "openrouter-models":
                return self.openrouter_models(args)
            case "spend":
                return self.spend()
            case "rate":
                return self.rate(args)
            case "prompts":
                self._show(PromptCatalogue.standard().render().rstrip("\n"))
                return 0
            case "calibrate":
                return self.calibrate(args)
            case "tune":
                return self.tune(args)
            case "audit":
                return self.audit(args)
            case "label":
                return self.label(args)
            case "escalations" | "resolve" | "resume" | "drop":
                return self.escalations(args)
            case "swarm":
                return self.swarm(args)
            case _:
                raise ValueError(f"unknown command {args.command!r}")

    def config(self, args: argparse.Namespace) -> int:
        config = ProjectConfig.load(Path(args.config))
        self._show(yaml.safe_dump(config.model_dump(mode="json", by_alias=True), sort_keys=False).rstrip())
        return 0

    def run(self, args: argparse.Namespace) -> int:
        return self.run_project(Path(args.config), Path(args.root).expanduser(), args.max_rounds, args.task)

    def run_project(self, path: Path, root: Path, max_rounds: int, tasks: Sequence[str] = ()) -> int:
        config = ProjectConfig.load(path)
        project = Project.open(config, root, ProjectServices(teams=self._catalog))
        try:
            for task in tasks:
                project.create_artifact(task)
            before, after = asyncio.run(self.scored_run(project, max_rounds))
            self._show(Report.render(project.views()))
            if before is not None and after is not None:
                self._show(Progress.render(before, after))
            hardening = HardeningSummary.render(project.board.current())
            if hardening is not None:
                self._show(hardening)
            self._show(self.levels(config, root))
            if config.brief is not None:  # a document: one file to read, the sections in order
                written = DocumentAssembly.write(root / "levels" / config.levels.base, root / "document.md")
                if written is not None:
                    self._show(f"the document: {written}")
            self._show(
                RunVerdict.render(project.board.current(), project.board.log.read(), config.levels.lowest)
            )
        finally:
            project.close()
        return 0

    @classmethod
    async def scored_run(cls, project: Project, max_rounds: int) -> tuple[Scored | None, Scored | None]:
        """Scores, runs until idle, scores again: in one event loop, since a model client made in one (a
        reader that is also a writer, say) fails in the next (2026-10-16: "Event loop is closed")."""
        before = await cls.scored(project)
        await project.run_until_idle(max_rounds)
        return before, await cls.scored(project)

    @staticmethod
    async def scored(project: Project) -> Scored | None:
        """Scores the first shared level and records it on the board (None without a definition of done as
        numbers: no `score` or `questions` section)."""
        if project.progress is None:
            return None
        scored = await project.progress.measure(project.views())
        project.board.append([scored])
        return scored

    @staticmethod
    def levels(config: ProjectConfig, root: Path) -> str:
        """Plain folders of each level's files, for a person to look at (`levels/<level>/` under the root)."""
        return LevelFolders.render(LevelFolders(root / "repo", root, config.levels).write())

    def status(self, args: argparse.Namespace) -> int:
        """Reads only the board: works while the project runs, and needs no models or keys."""
        config = ProjectConfig.load(Path(args.config))
        root = Path(args.root).expanduser()
        if not (root / "blackboard.sqlite").is_file():
            raise SystemExit(f"no project at {root}")
        with SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), SystemClock()) as log:
            self._show(Report.render(Blackboard(log).current()))
        if (root / "repo").is_dir():
            self._show(self.levels(config, root))
        return 0

    async def compare(self, args: argparse.Namespace) -> int:
        """Every task of an experiment under every setup; a line per run, then the table (Phase 8)."""
        experiment_path = Path(args.experiment)
        experiment = Experiment.load(experiment_path)
        workdir = Path(args.workdir).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        results = ComparisonFiles.results(workdir)
        if experiment.kind is ExperimentKind.REVIEW:
            review = ReviewRun(experiment, workdir)
            review_report = await review.run(lambda measure: self._show(ReviewReport.line(measure)))
            review_report.write(results)
            self._show("\n" + review_report.render())
            self._show(f"\nEvery run and the summary: {results}")
            return 0
        comparison = ComparisonRun(experiment, workdir)
        report = await comparison.run(lambda measure: self._show(ComparisonReport.line(measure)))
        report.write(results)
        self._show("\n" + report.render())
        self._show(f"\nEvery run and the summary: {results}")
        return 0

    async def probe(self, args: argparse.Namespace) -> int:
        """Every probe to every model, one prompt and one answer each, scored by plain code (Phase 8)."""
        experiment = ProbeExperiment.load(Path(args.experiment))
        workdir = Path(args.workdir).expanduser()
        run = ProbeRun(experiment, workdir, self._probe_workers, limits=SpendLimits.load())
        report = await run.run(lambda result: self._show(ProbeReport.line(result)))
        self._show("\n" + report.render())
        for reason in run.stopped:
            self._show(f"stopped early: {reason} (a rerun resumes)")
        self._show(
            f"\nEvery answer: {workdir / ProbeFiles.RESULTS}; the table: {workdir / ProbeFiles.SUMMARY}"
        )
        return 0

    def import_evalplus(self, args: argparse.Namespace) -> int:
        """An EvalPlus release file as a task suite; only tasks whose reference passes are written."""
        kind = EvalPlusFormat(args.format)
        out = Path(args.out).expanduser()
        tasks = EvalPlusReader.read(Path(args.file).expanduser(), kind)
        sandbox = Sandboxes.build(SandboxConfig())
        check = ImportCheck(sandbox, out / "_scratch", SuiteWriter.TEST_COMMAND, SuiteWriter.TIMEOUT_S)
        report = SuiteWriter(out, args.name or kind.value, check).write(tasks)
        self._show(report.render())
        self._show(f"Suite: {out / 'suite.yaml'}")
        return 0

    def import_commonmark(self, args: argparse.Namespace) -> int:
        """The CommonMark spec's examples as a seed project, every fifth held out (ROADMAP.md, stage 1)."""
        prose = Path(args.text).expanduser() if args.text else None
        summary = CommonMarkImport(Path(args.spec).expanduser(), Path(args.out).expanduser(), prose).write()
        self._show(summary.render("examples"))
        return 0

    def monitor(self, args: argparse.Namespace) -> int:
        """Serves projects' boards as a web page on localhost until interrupted; it only reads.

        The projects: CONFIG with --root, any number of --project CONFIG=ROOT, and every project of --swarm.
        """
        pairs: list[tuple[Path, Path]] = []
        if args.config is not None:
            if args.root is None:
                raise SystemExit("CONFIG needs --root")
            pairs.append((Path(args.config), Path(args.root)))
        for item in args.project:
            config, _, root = item.partition("=")
            if not root:
                raise SystemExit(f"--project takes CONFIG=ROOT, not {item!r}")
            pairs.append((Path(config), Path(root)))
        if args.swarm is not None:
            pairs += [(p.config, p.root) for p in SwarmConfig.load(Path(args.swarm)).projects]
        if not pairs:
            raise SystemExit("name a project: CONFIG --root DIR, --project CONFIG=ROOT, or --swarm FILE")
        projects: dict[str, Watched] = {}
        for config_path, root in pairs:
            config = ProjectConfig.load(config_path)
            if config.project in projects:
                raise SystemExit(f"two projects are called {config.project!r}")
            projects[config.project] = Watched(config=config, board=root.expanduser() / "blackboard.sqlite")
        server = MonitorServer(projects, port=args.port)
        self._show(f"monitoring {', '.join(projects)} at {server.url} (Ctrl-C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    def import_toml(self, args: argparse.Namespace) -> int:
        """The toml-test suite's TOML 1.0.0 files as a seed project, every fifth per group held out."""
        summary = TomlImport(
            Path(args.suite).expanduser(), Path(args.out).expanduser(), args.hide_all
        ).write()
        self._show(summary.render())
        return 0

    def import_jq(self, args: argparse.Namespace) -> int:
        """jq's own tests and the manual's examples as a seed project, every fifth per group held out."""
        summary = JqImport(Path(args.source).expanduser(), Path(args.out).expanduser()).write()
        self._show(summary.render())
        return 0

    def import_yaml(self, args: argparse.Namespace) -> int:
        """The yaml-test-suite's data release as a seed project, every fifth case per group held out."""
        spec = Path(args.spec).expanduser() if args.spec else None
        summary = YamlImport(
            Path(args.suite).expanduser(), Path(args.out).expanduser(), spec, args.hide_all
        ).write()
        self._show(summary.render())
        return 0

    def import_html5(self, args: argparse.Namespace) -> int:
        """The html5lib tree-construction tests as a seed project, every fifth per group held out."""
        spec = Path(args.spec).expanduser() if args.spec else None
        self._show(
            Html5Import(Path(args.tests).expanduser(), Path(args.out).expanduser(), spec).write().render()
        )
        return 0

    def import_guide(self, args: argparse.Namespace) -> int:
        """A newcomer's guide to some Qasper papers as a seed project; their questions held out."""
        qasper, out = Path(args.qasper).expanduser(), Path(args.out).expanduser()
        guide = GuideImport(qasper, args.papers, args.topic, out, args.words, args.purpose)
        self._show(guide.write().render())
        return 0

    def qasper_papers(self, args: argparse.Namespace) -> int:
        """Candidate papers for a guide: id, answerable questions, title."""
        papers = QasperSet(Path(args.qasper).expanduser()).search(args.words)
        self._show("\n".join(f"{p.id}  {len(p.questions):>2}  {p.title}" for p in papers) or "none")
        return 0

    LEARN = (
        "A tutorial, not a literature review, for someone who knows {know}, and wants to {want}, from the"
        " papers given. Organize it around concepts, principles and the questions the reader must "
        "understand, using the papers as evidence rather than as chapters. Introduce each idea before it "
        "is used; motivate each equation, define its symbols, and derive the important results where the "
        "papers allow; give a concrete example where it helps. Explain why each method works and which "
        "limitation of earlier approaches it addresses, and keep apart what is proven, what is shown by "
        "experiments, and what is the authors' hypothesis. Include a section that compares the methods "
        "side by side (what each predicts, its loss, how it avoids collapse or failure). Prefer "
        "understanding to exhaustive benchmark numbers. End with a short summary and a few questions the "
        "reader can answer to check their understanding."
    )

    def learn(self, args: argparse.Namespace) -> int:
        """Papers to a tutorial for one reader, in one command: import, brief, writing, a reader review."""
        files = [Path(f).expanduser() for f in args.files]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            self._show(f"not found: {', '.join(missing)}")
            return 2
        name = SourceText.slug(args.want, 40)
        out = Path(args.out).expanduser() if args.out else Path("~/agentswarm-data/learn").expanduser()
        goal = self.LEARN.format(know=args.know, want=args.want)
        perspectives = ("reader", "facts") if args.review else ()
        imported = DocumentImport(
            files, out, goal, (), args.words, perspectives, args.check_brief, args.budget, name
        ).write()
        self._show(imported.render())
        root = Path("~/agentswarm-runs").expanduser() / f"learn-{name}"
        status = self.run_project(imported.projects[0], root, args.max_rounds)
        if not args.check_brief:
            self._show(f"the tutorial: {root / 'document.md'}")
        return status

    def audit_citations(self, args: argparse.Namespace) -> int:
        """A random sample of a document's cited statements, each checked against its source by a referee."""
        config = ProjectConfig.load(Path(args.config))
        root = Path(args.root).expanduser()
        model = args.model or (config.review.referee if config.review is not None else "referee")
        if model not in config.models:
            self._show(f"no model {model!r} in the project: name one with --model")
            return 2
        project = Project.open(config, root, ProjectServices(teams=self._catalog))
        try:
            audit = CitationAudit(
                root / "levels" / config.levels.base, project.worker(model, "citation-audit")
            )
            results = asyncio.run(audit.audit(audit.sample(args.sample, args.seed)))
        finally:
            project.close()
        self._show(CitationAudit.render(results))
        return 0

    def import_docs(self, args: argparse.Namespace) -> int:
        """The user's own documents as sources, and a project per audience that drafts a brief first."""
        files = [Path(f).expanduser() for f in args.files]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            self._show(f"not found: {', '.join(missing)}")
            return 2
        imported = DocumentImport(files, Path(args.out).expanduser(), args.goal, args.audience, args.words)
        self._show(imported.write().render())
        return 0

    def questions(self, args: argparse.Namespace) -> int:
        """Reads only the board and the questions file: no models, no money."""
        config = ProjectConfig.load(Path(args.config))
        if config.questions is None:
            self._show("the project has no `questions` section")
            return 2
        root = Path(args.root).expanduser()
        questions = HiddenQuestions(config.questions.path.expanduser()).all()
        with SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), SystemClock()) as log:
            self._show(QuestionTrace.render(log.read(), questions))
        return 0

    def import_jsonpath(self, args: argparse.Namespace) -> int:
        """The JSONPath compliance test suite and RFC 9535 as a seed project; some or all tests held out."""
        suite, out = Path(args.suite).expanduser(), Path(args.out).expanduser()
        rfc = Path(args.rfc).expanduser() if args.rfc else None
        self._show(JsonPathImport(suite, rfc, out, args.hide_all).write().render())
        return 0

    def score(self, args: argparse.Namespace) -> int:
        """The share of a test suite that passes on the project's first shared level."""
        config = ProjectConfig.load(Path(args.config))
        defined = config.score is not None or config.questions is not None
        if defined and args.check is None and not args.extra:  # the config's definition of done
            project = Project.open(config, Path(args.root).expanduser(), ProjectServices(teams=self._catalog))
            try:
                scored = asyncio.run(self.scored(project))
            finally:
                project.close()
            assert scored is not None
            self._show(Report.line(scored))
            return 0
        name = args.check or (config.worklist.check if config.worklist is not None else None)
        if name is None or name not in config.checks:
            self._show("name the test check with --check (or give the project a worklist)")
            return 2
        check = config.checks[name]
        board = Scoreboard(
            Path(args.root).expanduser() / "repo",
            Sandboxes.build(config.sandbox),
            check.command,
            check.timeout_s,
        )
        extra = [Path(e).expanduser().resolve() for e in args.extra]
        groups = [("acceptance tests (seen by the agents)", "tests/acceptance/")]
        groups += [(f"held out ({e.name})", f"{Scoreboard.EXTRA}/{e.name}/") for e in extra]
        self._show(board.score(config.levels.base, extra).render(groups))
        return 0

    def openrouter_models(self, args: argparse.Namespace) -> int:
        """`models:` entries for OpenRouter models, priced from its live list (no key needed to read it)."""
        catalog = OpenRouterCatalog()
        entries = catalog.entries(args.ids)
        self._show(OpenRouterCatalog.render(entries, date.today(), catalog.tiered(args.ids)))
        return 0

    def spend(self) -> int:
        """Today's spend across every run, from the machine-wide ledger (~/agentswarm-data/limits.yaml)."""
        limits = SpendLimits.load()
        if limits is None:
            self._show(f"no spending limits: write {SpendLimits.FILE}")
            return 0
        shared = SharedSpend(limits, SystemClock())
        try:
            self._show(shared.report())
        finally:
            shared.close()
        return 0

    def rate(self, args: argparse.Namespace) -> int:
        """Jev rates how hard every task of the suites is, before any model tries them (Phase 8)."""
        config = JevConfig()
        decider = self._rating_decider or JevDecider(JevClient.from_env(config.model, config.timeout_s))
        ratings = DifficultyRatings(Path(args.out).expanduser())
        rater = DifficultyRater(decider, ratings)
        for suite in args.suites:
            rated, skipped, failed = rater.rate(Path(suite).expanduser(), self._show)
            self._show(f"{suite}: {rated} rated, {skipped} already rated, {failed} failed")
        return 0

    def routing(self, args: argparse.Namespace) -> int:
        """What the router believes: per kind of call and model, outcomes and the believed rate per band."""
        path = Path(args.ledger).expanduser()
        if not path.is_file():
            raise SystemExit(f"no routing ledger at {path}")
        ledger = RoutingLedger(path)
        try:
            half_life = RoutingConfig().half_life_calls  # the default discount, as routers use it
            self._show(Beliefs(ledger.calls(), Prior(), half_life).render())
        finally:
            ledger.close()
        return 0

    async def evaluate(self, args: argparse.Namespace) -> int:
        suite = TaskSuite.load(Path(args.suite))
        workdir = Path(args.workdir).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        variants = tuple(Variant(v) for v in args.variant) if args.variant else tuple(Variant)
        with SqliteDecisions(workdir / "decisions.sqlite") as decisions:
            run = EvaluationRun(
                suite, workdir, decisions, self._evaluation_services, variants, active=args.active
            )
            for task in suite.tasks:
                for variant in variants:
                    result = await run.run_task(task, variant)
                    self._show(EvaluationReport.line(result))
            self._show("\n" + CalibrationReport.of(decisions).render())
            if args.active:
                self._show("\n" + GateAudit.render_all(GateAudit.cohorts(decisions)))
        return 0

    def calibrate(self, args: argparse.Namespace) -> int:
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            self._show(CalibrationReport.of(decisions).render())
        return 0

    def tune(self, args: argparse.Namespace) -> int:
        tuner = ThresholdTuner(args.precision, args.min_support)
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            judges = JudgeCatalog.standard()
            self._show(TuningReport.of(tuner.tune(decisions, judges), judges).render())
        return 0

    def audit(self, args: argparse.Namespace) -> int:
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            self._show(GateAudit.render_all(GateAudit.cohorts(decisions, args.point)))
        return 0

    def label(self, args: argparse.Namespace) -> int:
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            check = SpotCheck(decisions, JudgeCatalog.standard(), self._ask, self._show, SystemClock())
            self._show(check.run(args.limit).render())
        return 0

    def swarm(self, args: argparse.Namespace) -> int:
        """Closes the projects only after `asyncio.run` returns, which waits for their worker threads."""
        swarm = Swarm.open(SwarmConfig.load(Path(args.swarm)))
        try:
            for item in args.task:
                name, _, task = item.partition("=")
                if name not in swarm.projects or not task:
                    raise SystemExit(
                        f"--task must look like PROJECT=TEXT with a project of the swarm: {item!r}"
                    )
                swarm.projects[name].create_artifact(task)
            runs = asyncio.run(swarm.run(args.max_rounds))
            for run in runs:
                report = Report.render(swarm.projects[run.project].views())
                self._show(f"=== {run.project} ({run.status})\n{report}\n")
        finally:
            swarm.close()
        return 1 if any(run.failed for run in runs) else 0

    def escalations(self, args: argparse.Namespace) -> int:
        """Reads and writes only the board and the decision log: no models or Jev key needed."""
        config = ProjectConfig.load(Path(args.config))
        root = Path(args.root).expanduser()
        if not (root / "blackboard.sqlite").is_file():
            raise SystemExit(f"no project at {root}")
        decisions = (
            SqliteDecisions(root / "decisions.sqlite") if (root / "decisions.sqlite").is_file() else None
        )
        with SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), SystemClock()) as log:
            board = Blackboard(log)
            queue = Escalations(board, decisions)
            try:
                match args.command:
                    case "resolve":
                        queue.resolve(ArtifactId(args.artifact), Resolution(args.resolution), args.note)
                        self._show(f"{args.artifact}: {args.resolution}")
                    case "resume":
                        queue.resume(args.note)
                        self._show("resumed")
                    case "drop":
                        dropped = queue.drop(ArtifactId(args.artifact), args.note)
                        self._show(f"dropped {len(dropped)}: {', '.join(dropped)}")
                    case _:
                        self._show(Escalations.render(queue.open(), board.current().paused))
            except ValueError as error:
                raise SystemExit(str(error)) from error
            finally:
                if decisions is not None:
                    decisions.close()
        return 0

    @staticmethod
    def _existing(path: str) -> Path:
        decisions = Path(path).expanduser()
        if not decisions.is_file():
            raise SystemExit(f"no decision log at {decisions}")
        return decisions

    @staticmethod
    def parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="agentswarm")
        commands = parser.add_subparsers(dest="command", required=True)
        config = commands.add_parser("config", help="print the effective configuration (preset applied)")
        config.add_argument("config", help="the project's YAML configuration")
        run = commands.add_parser("run", help="add tasks (optional) and run until idle")
        status = commands.add_parser("status", help="show every artifact's history")
        for command in (run, status):
            command.add_argument("config", help="the project's YAML configuration")
            command.add_argument(
                "--root", required=True, help="directory holding the project's log and repository"
            )
        run.add_argument("--task", action="append", default=[], help="a task to add; may be repeated")
        run.add_argument("--max-rounds", type=int, default=50)

        compare = commands.add_parser(
            "compare", help="run a suite's tasks under several setups and compare them"
        )
        compare.add_argument("experiment", help="the experiment's YAML file: a suite and setups")
        compare.add_argument("--workdir", required=True, help="a new directory for the projects and results")

        routing = commands.add_parser("routing", help="what the router has learned from outcomes")
        routing.add_argument("ledger", help="the routing ledger (SQLite)")

        probe = commands.add_parser("probe", help="put one-prompt probes to several models and score them")
        probe.add_argument("experiment", help="the probe experiment's YAML file: suites, kinds and models")
        probe.add_argument("--workdir", required=True, help="where results go; rerun to resume")

        evalplus = commands.add_parser("import-evalplus", help="turn an EvalPlus release into a task suite")
        evalplus.add_argument("file", help="an EvalPlus OriginFmt release file (.jsonl.gz)")
        evalplus.add_argument("--format", required=True, choices=[f.value for f in EvalPlusFormat])
        evalplus.add_argument("--out", required=True, help="the suite's folder")
        evalplus.add_argument("--name", help="the project name (default: the format)")

        commonmark = commands.add_parser("import-commonmark", help="the CommonMark spec as a seed project")
        commonmark.add_argument("spec", help="the spec's examples (spec.json from spec.commonmark.org)")
        commonmark.add_argument("--out", required=True, help="where the seed and the held-out tests go")
        commonmark.add_argument(
            "--text", help="spec.txt: the spec's text, put in the seed without its examples"
        )
        monitor = commands.add_parser("monitor", help="a read-only web page of a project, on localhost")
        monitor.add_argument("config", nargs="?", help="a project's YAML configuration (with --root)")
        monitor.add_argument("--root", help="directory holding that project")
        monitor.add_argument(
            "--project", action="append", default=[], help="another project, as CONFIG=ROOT (repeatable)"
        )
        monitor.add_argument("--swarm", help="a swarm file: every project in it")
        monitor.add_argument("--port", type=int, default=8765, help="the port on 127.0.0.1 (default 8765)")
        toml = commands.add_parser("import-toml", help="the toml-test suite as a seed project")
        toml.add_argument("suite", help="a checkout of github.com/toml-lang/toml-test")
        toml.add_argument("--out", required=True, help="where the seed and the held-out tests go")
        toml.add_argument(
            "--hide-all",
            action="store_true",
            help="hold out every test: the spec and no labels (self-supervised)",
        )
        jq = commands.add_parser("import-jq", help="jq's own tests as a seed project")
        jq.add_argument("source", help="a checkout of github.com/jqlang/jq at a release tag")
        jq.add_argument("--out", required=True, help="where the seed and the held-out tests go")
        yml = commands.add_parser("import-yaml", help="the yaml-test-suite as a seed project")
        yml.add_argument("suite", help="a checkout of github.com/yaml/yaml-test-suite at a data-* tag")
        yml.add_argument("--out", required=True, help="where the seed and the held-out tests go")
        yml.add_argument(
            "--spec", help="the YAML 1.2.2 spec's Markdown (yaml-spec), for the agents without examples"
        )
        yml.add_argument(
            "--hide-all",
            action="store_true",
            help="hold out every test: the spec and no labels (self-supervised)",
        )
        html5 = commands.add_parser(
            "import-html5", help="the html5lib tree-construction tests as a seed project"
        )
        html5.add_argument(
            "tests", help="a folder of the tests' .dat files (web-platform-tests, parsing/resources)"
        )
        html5.add_argument("--out", required=True, help="where the seed and the held-out tests go")
        html5.add_argument("--spec", help="the standard's parsing chapter as text, for the agents")

        jsonpath = commands.add_parser(
            "import-jsonpath", help="the JSONPath compliance suite as a seed project"
        )
        jsonpath.add_argument(
            "suite", help="a checkout of github.com/jsonpath-standard/jsonpath-compliance-test-suite"
        )
        jsonpath.add_argument("--rfc", help="RFC 9535 as plain text, the spec for the agents; none: no spec")
        jsonpath.add_argument("--out", required=True, help="where the seed and the held-out tests go")
        jsonpath.add_argument("--hide-all", action="store_true", help="hold out every test (no labels)")

        guide = commands.add_parser("import-guide", help="a guide to Qasper papers as a seed project")
        guide.add_argument("qasper", help="one of Qasper's JSON files, e.g. qasper-test-v0.3.json")
        guide.add_argument("--out", required=True, help="where the seed and the held-out questions go")
        guide.add_argument("--topic", required=True, help="what the papers are about, for the brief")
        guide.add_argument("--papers", required=True, nargs="+", help="the papers' ids, in reading order")
        guide.add_argument("--words", type=int, default=3000, help="the guide's length cap")
        guide.add_argument(
            "--purpose", default="reference", choices=list(GuideImport.PURPOSES), help="what the guide is for"
        )
        papers = commands.add_parser(
            "qasper-papers", help="Qasper papers whose title or abstract has the words"
        )
        papers.add_argument("qasper", help="one of Qasper's JSON files")
        papers.add_argument("words", nargs="+", help="words that must all appear, ignoring case")

        docs = commands.add_parser("import-docs", help="your documents as sources, a project per audience")
        docs.add_argument("files", nargs="+", help="the documents: PDF, Word, HTML, text, Markdown")
        docs.add_argument("--out", required=True, help="where the seed and the project files go")
        docs.add_argument("--goal", required=True, help="what you want, e.g. 'A summary of these reports.'")
        docs.add_argument(
            "--audience", action="append", default=[], help="a reader to write for; repeat for several"
        )
        docs.add_argument("--words", type=int, default=3000, help="the document's length cap")

        learn = commands.add_parser("learn", help="papers to a tutorial for you, in one command")
        learn.add_argument("files", nargs="+", help="the papers: PDF, Word, HTML, text, Markdown")
        learn.add_argument("--know", required=True, help="what you know already, e.g. 'Python and basic ML'")
        learn.add_argument("--want", required=True, help="what you want, e.g. 'understand diffusion models'")
        learn.add_argument("--words", type=int, default=4000, help="the tutorial's length cap")
        learn.add_argument("--review", action="store_true", help="one round of reader and fact review")
        learn.add_argument("--check-brief", action="store_true", help="pause after the brief for you to read")
        learn.add_argument("--budget", type=float, default=1.0, help="the run's cap in dollars")
        learn.add_argument("--out", help="where the project goes (default ~/agentswarm-data/learn)")
        learn.add_argument("--max-rounds", type=int, default=300)

        cites = commands.add_parser("audit-citations", help="check a sample of a document's citations")
        cites.add_argument("config", help="the project's YAML configuration")
        cites.add_argument("--root", required=True, help="directory holding the project's log and levels")
        cites.add_argument("--sample", type=int, default=20, help="statements to check")
        cites.add_argument("--seed", type=int, default=1, help="the sample's seed, for a repeatable draw")
        cites.add_argument("--model", help="the checking model (default: the project's referee)")

        traced = commands.add_parser("questions", help="a document's hidden questions, score by score")
        traced.add_argument("config", help="the project's YAML configuration")
        traced.add_argument("--root", required=True, help="directory holding the project's log")

        score = commands.add_parser("score", help="how far the first shared level is through a test suite")
        score.add_argument("config", help="the project's YAML configuration")
        score.add_argument("--root", required=True, help="directory holding the project's log and repository")
        score.add_argument("--check", help="the test check to run (default: the worklist's)")
        score.add_argument(
            "--extra", action="append", default=[], help="a folder of extra tests, e.g. held out"
        )

        openrouter = commands.add_parser("openrouter-models", help="model entries with OpenRouter's prices")
        openrouter.add_argument("ids", nargs="+", help="OpenRouter model ids, e.g. qwen/qwen3-coder")

        commands.add_parser("spend", help="today's spend of every run, per account, against the limits")

        rate = commands.add_parser("rate", help="Jev rates every task's difficulty (resumable)")
        rate.add_argument("suites", nargs="+", help="suite files")
        rate.add_argument("--out", required=True, help="the ratings file (JSON Lines); a rerun resumes it")

        commands.add_parser("prompts", help="print every model call and Jev question (docs/PROMPTS.md)")

        evaluate = commands.add_parser("evaluate", help="run a task suite and print the calibration report")
        evaluate.add_argument("suite", help="the suite's YAML file")
        evaluate.add_argument(
            "--workdir", required=True, help="a new directory for this run's projects and log"
        )
        evaluate.add_argument(
            "--variant",
            action="append",
            choices=[v.value for v in Variant],
            help="run only these variants; may be repeated (default: all)",
        )
        evaluate.add_argument(
            "--active",
            action="append",
            default=[],
            help="a decision point to run active (glob); may be repeated",
        )
        audit = commands.add_parser("audit", help="how often an active gate was wrong")
        audit.add_argument("decisions", help="a decisions.sqlite file")
        audit.add_argument("--point", default="gate:raw->experimental", help="the gate's decision point")
        calibrate = commands.add_parser("calibrate", help="the calibration report for a decision log")
        calibrate.add_argument("decisions", help="a decisions.sqlite file")
        tune = commands.add_parser("tune", help="thresholds from a decision log, per question")
        tune.add_argument("decisions", help="a decisions.sqlite file")
        tune.add_argument(
            "--precision", type=float, default=0.95, help="how often a decided answer must be right"
        )
        tune.add_argument(
            "--min-support", type=int, default=20, help="answers needed before a point can go active"
        )
        label = commands.add_parser("label", help="spot-check automatic outcomes by hand")
        label.add_argument("decisions", help="a decisions.sqlite file")
        label.add_argument("--limit", type=int, default=20, help="how many decisions to show")

        swarm = commands.add_parser("swarm", help="run several projects at once, sharing one scheduler")
        swarm.add_argument("swarm", help="the swarm's YAML file")
        swarm.add_argument("--task", action="append", default=[], help="PROJECT=TEXT: a task for one project")
        swarm.add_argument("--max-rounds", type=int, default=50)
        escalations = commands.add_parser("escalations", help="list open escalations")
        resolve = commands.add_parser("resolve", help="resolve an escalation")
        resume = commands.add_parser("resume", help="lift a drift pause")
        drop = commands.add_parser("drop", help="retire a task and every step split from it")
        for command in (escalations, resolve, resume, drop):
            command.add_argument("config", help="the project's YAML configuration")
            command.add_argument("--root", required=True, help="directory holding the project")
        resolve.add_argument("artifact", help="the escalated artifact, e.g. a-0003")
        drop.add_argument("artifact", help="the task, e.g. a-0032; its split steps go with it")
        choice = resolve.add_mutually_exclusive_group(required=True)
        for resolution in Resolution:
            choice.add_argument(
                f"--{resolution.value}", dest="resolution", action="store_const", const=resolution.value
            )
        for command in (resolve, resume, drop):
            command.add_argument("--note", default="", help="why, for the record")
        return parser


class EvaluationReport:
    """One line per evaluation run."""

    @staticmethod
    def line(result: TaskResult) -> str:
        hidden = (
            ",".join({True: "pass", False: "fail", None: "error"}[p] for p in result.hidden_tests_passed)
            or "-"
        )
        mutation = ",".join("-" if s is None else f"{s:.2f}" for s in result.mutation_scores) or "-"
        note = f"  ({result.note[:80]})" if result.note else ""
        return (
            f"{result.task:20} {result.variant:14} {result.level:13} decisions={len(result.decisions)} "
            f"hidden={hidden} mutation={mutation}{note}"
        )
