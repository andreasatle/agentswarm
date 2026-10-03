"""Command line: run a project on a task and show what happened to its artifacts."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from agentswarm.board.blackboard import BoardViews
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import (
    ArtifactCreated,
    Claimed,
    CommitPushed,
    Escalated,
    Event,
    FindingAdded,
    Promoted,
    Rejected,
    Released,
)
from agentswarm.project import Project, ProjectServices
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
                return f"commit {event.commit[:10]}"
            case FindingAdded():
                return f"finding ({event.finding.kind}): {event.finding.detail}"
            case Promoted():
                return f"promoted {event.from_level} -> {event.to_level}"
            case Rejected():
                return "rejected: " + "; ".join(f"{f.kind}: {f.detail}" for f in event.findings)
            case Escalated():
                return f"ESCALATED: {event.reason}"

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
    """`agentswarm run CONFIG --root DIR [--task TEXT]...` and `agentswarm status CONFIG --root DIR`."""

    def __init__(self, catalog: TeamCatalog | None = None) -> None:
        self._catalog = catalog or TeamCatalog.standard()

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> int:
        return cls().dispatch(sys.argv[1:] if argv is None else argv)

    def dispatch(self, argv: Sequence[str]) -> int:
        args = self.parser().parse_args(argv)
        config = ProjectConfig.load(Path(args.config))
        project = Project.open(config, Path(args.root).expanduser(), ProjectServices(teams=self._catalog))
        try:
            if args.command == "run":
                for task in args.task:
                    project.create_artifact(task)
                asyncio.run(project.run_until_idle(args.max_rounds))
            print(Report.render(project.views()))
        finally:
            project.close()
        return 0

    @staticmethod
    def parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="agentswarm")
        commands = parser.add_subparsers(dest="command", required=True)
        run = commands.add_parser("run", help="add tasks (optional) and run until idle")
        status = commands.add_parser("status", help="show every artifact's history")
        for command in (run, status):
            command.add_argument("config", help="the project's YAML configuration")
            command.add_argument(
                "--root", required=True, help="directory holding the project's log and repository"
            )
        run.add_argument("--task", action="append", default=[], help="a task to add; may be repeated")
        run.add_argument("--max-rounds", type=int, default=50)
        return parser
