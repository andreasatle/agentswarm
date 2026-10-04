"""Command line: run a project on a task and show what happened to its artifacts."""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.control.escalations import Escalations
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import (
    ArtifactCreated,
    ArtifactId,
    Claimed,
    CommitPushed,
    Escalated,
    EscalationResolved,
    Event,
    FindingAdded,
    FindingResolved,
    Paused,
    ProjectId,
    Promoted,
    PromotionSettled,
    PromotionStarted,
    Rejected,
    Released,
    Resolution,
    Resumed,
    SignedOff,
    Sized,
    Spent,
)
from agentswarm.evaluation.faults import Variant
from agentswarm.evaluation.run import EvaluationRun, TaskResult
from agentswarm.evaluation.suite import EvalTask, TaskSuite
from agentswarm.judging.audit import GateAudit
from agentswarm.judging.calibration import CalibrationReport
from agentswarm.judging.decisions import SqliteDecisions
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.judging.labelling import SpotCheck
from agentswarm.judging.tuning import ThresholdTuner, TuningReport
from agentswarm.project import Project, ProjectServices
from agentswarm.swarm import Swarm, SwarmConfig
from agentswarm.teams.catalog import TeamCatalog


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
            case Paused():
                return f"PAUSED: {event.reason}"
            case Resumed():
                return f"resumed: {event.reason}"
            case Sized():
                return "fits one branch" if event.fits else f"split into {', '.join(event.children)}"
            case Promoted():
                return f"promoted {event.from_level} -> {event.to_level}"
            case PromotionStarted():
                return f"moving {event.branch} to {event.target[:10]}"
            case PromotionSettled():
                return f"{event.branch} {'moved' if event.moved else 'did not move'}"
            case Rejected():
                return "rejected: " + "; ".join(f"{f.kind}: {f.detail}" for f in event.findings)
            case Escalated():
                return f"ESCALATED: {event.reason}"
            case EscalationResolved():
                note = f": {event.note}" if event.note else ""
                return f"escalation resolved ({event.resolution}){note}"

    @classmethod
    def render(cls, views: BoardViews) -> str:
        blocks: list[str] = []
        for artifact in views.artifacts():
            lines = [f"{artifact.id} [{artifact.level}] {artifact.task}"]
            lines += [
                f"  - {cls.line(e)}"
                for e in views.history(artifact.id)
                if not isinstance(e, Claimed | Released)
            ]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) or "(no artifacts)"


class Cli:
    """The `agentswarm` command line.

        config CONFIG                              the effective configuration, with the domain preset applied
        run CONFIG --root DIR [--task TEXT]...     add tasks and run the project until idle
        status CONFIG --root DIR                   every artifact's history
        evaluate SUITE --workdir DIR [--active P]  run a task suite; record outcomes; print calibration
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
    ) -> None:
        self._catalog = catalog or TeamCatalog.standard()
        self._show = show
        self._ask = ask
        self._evaluation_services = evaluation_services

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> int:
        load_dotenv(Path.cwd() / ".env")  # never overrides variables already set
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
                return asyncio.run(self.swarm(args))
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
            asyncio.run(project.run_until_idle(args.max_rounds))
            self._show(Report.render(project.views()))
        finally:
            project.close()
        return 0

    def status(self, args: argparse.Namespace) -> int:
        """Reads only the board: works while the project runs, and needs no models or keys."""
        config = ProjectConfig.load(Path(args.config))
        root = Path(args.root).expanduser()
        if not (root / "blackboard.sqlite").is_file():
            raise SystemExit(f"no project at {root}")
        with SqliteEventLog(root / "blackboard.sqlite", ProjectId(config.project), SystemClock()) as log:
            self._show(Report.render(Blackboard(log).current()))
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
                self._show("\n" + GateAudit.of(decisions).render())
        return 0

    def calibrate(self, args: argparse.Namespace) -> int:
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            self._show(CalibrationReport.of(decisions).render())
        return 0

    def tune(self, args: argparse.Namespace) -> int:
        tuner = ThresholdTuner(args.precision, args.min_support)
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            self._show(TuningReport.of(tuner.tune(decisions, JudgeCatalog.standard())).render())
        return 0

    def audit(self, args: argparse.Namespace) -> int:
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            self._show(GateAudit.of(decisions, args.point).render())
        return 0

    def label(self, args: argparse.Namespace) -> int:
        with SqliteDecisions(self._existing(args.decisions)) as decisions:
            check = SpotCheck(decisions, JudgeCatalog.standard(), self._ask, self._show, SystemClock())
            self._show(check.run(args.limit).render())
        return 0

    async def swarm(self, args: argparse.Namespace) -> int:
        swarm = Swarm.open(SwarmConfig.load(Path(args.swarm)))
        try:
            for item in args.task:
                name, _, task = item.partition("=")
                if name not in swarm.projects or not task:
                    raise SystemExit(
                        f"--task must look like PROJECT=TEXT with a project of the swarm: {item!r}"
                    )
                swarm.projects[name].create_artifact(task)
            for run in await swarm.run(args.max_rounds):
                status = f"{run.rounds} rounds" if run.rounds is not None else f"still busy: {run.error}"
                self._show(
                    f"=== {run.project} ({status})\n{Report.render(swarm.projects[run.project].views())}\n"
                )
        finally:
            swarm.close()
        return 0

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
        hidden = ",".join("pass" if p else "fail" for p in result.hidden_tests_passed) or "-"
        mutation = ",".join("-" if s is None else f"{s:.2f}" for s in result.mutation_scores) or "-"
        note = f"  ({result.note[:80]})" if result.note else ""
        return (
            f"{result.task:20} {result.variant:14} {result.level:13} decisions={len(result.decisions)} "
            f"hidden={hidden} mutation={mutation}{note}"
        )
