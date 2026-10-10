"""Command line: run a project on a task and show what happened to its artifacts."""

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agentswarm.board.blackboard import Blackboard, BoardViews
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
    AcceptanceRestored,
    AcceptanceWritten,
    ArtifactCreated,
    ArtifactId,
    AttemptFailed,
    Claimed,
    CommitPushed,
    Escalated,
    EscalationResolved,
    Event,
    FindingAdded,
    FindingResolved,
    OutcomeRecorded,
    Paused,
    Profiled,
    ProjectId,
    Promoted,
    PromotionSettled,
    PromotionStarted,
    Rejected,
    Released,
    Resolution,
    Resumed,
    Routed,
    Scored,
    SignedOff,
    Sized,
    Spent,
    ToolsUsed,
)
from agentswarm.evaluation.commonmark import CommonMarkImport
from agentswarm.evaluation.compare import (
    ComparisonFiles,
    ComparisonReport,
    ComparisonRun,
    Experiment,
    ExperimentKind,
)
from agentswarm.evaluation.difficulty import DifficultyRater, DifficultyRatings
from agentswarm.evaluation.faults import Variant
from agentswarm.evaluation.imports import EvalPlusFormat, EvalPlusReader, ImportCheck, SuiteWriter
from agentswarm.evaluation.probe import ProbeExperiment, ProbeFiles, ProbeReport, ProbeRun
from agentswarm.evaluation.review import ReviewReport, ReviewRun
from agentswarm.evaluation.run import EvaluationRun, TaskResult
from agentswarm.evaluation.score import Progress, Scoreboard
from agentswarm.evaluation.suite import EvalTask, TaskSuite
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
from agentswarm.project import Project, ProjectServices
from agentswarm.prompts import PromptCatalogue
from agentswarm.swarm import Swarm, SwarmConfig
from agentswarm.teams.catalog import TeamCatalog
from agentswarm.teams.routing import Beliefs, Prior, RoutingLedger
from agentswarm.workspace.sandbox import Sandboxes
from agentswarm.workspace.view import LevelFolders


class Report:
    """Renders an artifact's history as one line per event."""

    @staticmethod
    def line(event: Event) -> str:
        match event:
            case ArtifactCreated():
                return f"created at {event.artifact.level}: {event.artifact.task}"
            case Claimed():
                return f"claimed by {event.claim.team}"
            case Released():
                return f"released by {event.team}"
            case CommitPushed():
                by = f" by {event.model}" if event.model else ""
                return f"commit {event.commit[:10]}{by}"
            case FindingAdded():
                return f"finding {event.finding.id} ({event.finding.kind}): {event.finding.detail}"
            case FindingResolved():
                by = f" by {event.resolved_by}" if event.resolved_by else ""
                return f"finding {event.finding} resolved{by}: {event.reason}"
            case SignedOff():
                return f"signed off by {event.team}"
            case Spent():
                cost = f"${event.usd:.4f}" if event.priced else "unpriced"
                tokens = f"{event.input_tokens}+{event.output_tokens} tokens"
                return f"spent by {event.by} on {event.model}: {tokens}, {cost}"
            case Scored():
                held = f", held out {Progress.tally(event.held_out)}" if event.held_out is not None else ""
                return f"scored {event.level} at {event.commit[:10]}: seen {Progress.tally(event.seen)}{held}"
            case Paused():
                return f"PAUSED: {event.reason}"
            case Resumed():
                return f"resumed: {event.reason}"
            case Sized():
                return "fits one branch" if event.fits else f"split into {', '.join(event.children)}"
            case Promoted():
                return f"promoted {event.from_level} -> {event.to_level}"
            case Routed():
                score = event.difficulty
                difficulty = f"difficulty {score:.2f}" if score is not None else "no score"
                how = "chosen from the ledger" if event.acted else "cheapest tier"
                return f"{event.call_kind} routed to {event.model} ({difficulty}, {how})"
            case Profiled():
                return f"profiled: difficulty {event.difficulty:.2f}{'' if event.active else ' (shadow)'}"
            case AttemptFailed():
                return f"attempt by {event.model or event.team} discarded: {event.reason}"
            case ToolsUsed():
                tools = Counter(c.tool for c in event.calls)
                read = sum(c.output_chars for c in event.calls)
                listed = ", ".join(f"{t} x{n}" for t, n in tools.most_common())
                return f"tool calls by {event.model or event.team}: {listed}; {read:,} characters returned"
            case OutcomeRecorded():
                verdict = "success" if event.success else "failure"
                return f"outcome for routing: {event.call_kind} by {event.model_id}, {verdict}"
            case AcceptanceWritten():
                return f"acceptance tests by {event.team}: {', '.join(f.path for f in event.files)}"
            case AcceptanceRestored():
                return f"acceptance tests restored after the producer changed them: {', '.join(event.paths)}"
            case PromotionStarted():
                return f"moving {event.branch} to {event.target[:10]}"
            case PromotionSettled():
                return f"{event.branch} {'moved' if event.moved else 'did not move'}"
            case Rejected():
                return "rejected: " + "; ".join(f"{f.kind}: {f.detail}" for f in event.findings)
            case Escalated():
                label = "ESCALATED" if event.parks else "ESCALATED (notice; others carry on)"
                return f"{label}: {event.reason}"
            case EscalationResolved():
                note = f": {event.note}" if event.note else ""
                return f"escalation resolved ({event.resolution}){note}"

    @classmethod
    def render(cls, views: BoardViews) -> str:
        usd, unpriced = views.spent()
        header = [f"spent: ${usd:.2f}" + (f" ({unpriced} unpriced model runs)" if unpriced else "")]
        header += [f"ESCALATED (project): {e.reason}" for e in views.project_escalations()[-5:]]
        header += ["PAUSED"] if views.paused else []
        header += [cls.line(views.scores()[-1])] if views.scores() else []
        blocks: list[str] = ["\n".join(header)]
        for artifact in views.artifacts():
            lines = [f"{artifact.id} [{artifact.level}] {artifact.task}"]
            lines += [
                f"  - {cls.line(e)}"
                for e in views.history(artifact.id)
                if not isinstance(e, Claimed | Released)
            ]
            blocks.append("\n".join(lines))
        if len(blocks) == 1:
            blocks.append("(no artifacts)")
        return "\n\n".join(blocks)


class Cli:
    """The `agentswarm` command line.

        config CONFIG                              the effective configuration, with the domain preset applied
        run CONFIG --root DIR [--task TEXT]...     add tasks and run the project until idle
        status CONFIG --root DIR                   every artifact's history
        evaluate SUITE --workdir DIR [--active P]  run a task suite; record outcomes; print calibration
        routing LEDGER                             what the router has learned: outcomes per model and band
        probe EXPERIMENT --workdir DIR             one prompt, one answer: models scored on single calls
        import-evalplus FILE --format F --out DIR  an EvalPlus benchmark as a task suite, references checked
        import-commonmark SPEC --out DIR           the CommonMark spec as a seed project and held-out tests
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
            case "escalations" | "resolve" | "resume":
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
        config = ProjectConfig.load(Path(args.config))
        project = Project.open(config, Path(args.root).expanduser(), ProjectServices(teams=self._catalog))
        try:
            for task in args.task:
                project.create_artifact(task)
            progress = self.progress(config, Path(args.root).expanduser())
            before = self.scored(progress, project)
            asyncio.run(project.run_until_idle(args.max_rounds))
            self._show(Report.render(project.views()))
            after = self.scored(progress, project)
            if before is not None and after is not None:
                self._show(Progress.render(before, after))
            self._show(self.levels(config, Path(args.root).expanduser()))
        finally:
            project.close()
        return 0

    @staticmethod
    def progress(config: ProjectConfig, root: Path) -> Progress | None:
        if config.score is None:
            return None
        return Progress(config, config.score, root / "repo", Sandboxes.build(config.sandbox))

    @staticmethod
    def scored(progress: Progress | None, project: Project) -> Scored | None:
        """Scores the first shared level and records it on the board (None without a `score` section)."""
        if progress is None:
            return None
        scored = progress.measure(project.views())
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
        self._show(
            f"{summary.sections} sections: {summary.visible} examples in {summary.seed}, "
            f"{summary.held_out} held out in {summary.held_out_tests}"
        )
        return 0

    def score(self, args: argparse.Namespace) -> int:
        """The share of a test suite that passes on the project's first shared level."""
        config = ProjectConfig.load(Path(args.config))
        progress = self.progress(config, Path(args.root).expanduser())
        if progress is not None and args.check is None and not args.extra:  # the config's definition of done
            root = Path(args.root).expanduser()
            with SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), SystemClock()) as log:
                board = Blackboard(log)
                scored = progress.measure(board.current())
                board.append([scored])
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
        for command in (escalations, resolve, resume):
            command.add_argument("config", help="the project's YAML configuration")
            command.add_argument("--root", required=True, help="directory holding the project")
        resolve.add_argument("artifact", help="the escalated artifact, e.g. a-0003")
        choice = resolve.add_mutually_exclusive_group(required=True)
        for resolution in Resolution:
            choice.add_argument(
                f"--{resolution.value}", dest="resolution", action="store_const", const=resolution.value
            )
        for command in (resolve, resume):
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
