"""Several projects in one process: Phase 6's exit criterion, with fake models that cost money."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from agentswarm.board.spend import Meter
from agentswarm.cli import Report
from agentswarm.core.clock import FakeClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Escalated, Usage
from agentswarm.judging.decisions import InMemoryDecisions
from agentswarm.llm.workers import Toolbox, Worker
from agentswarm.project import ProjectServices
from agentswarm.swarm import Swarm, SwarmConfig
from agentswarm.teams.executor import ExecutorReport, ExecutorRequest, WorktreeToolbox
from tests.support.samples import Samples


class Flight:
    """Counts model runs in flight across every project, to show they overlap."""

    def __init__(self) -> None:
        self.now = 0
        self.peak = 0


class CostlyWorker:
    """A fake executor model: writes a note, takes a moment, and costs a million input tokens."""

    def __init__(self, meter: Meter | None, flight: Flight) -> None:
        self._meter = meter
        self._flight = flight

    async def run[T: BaseModel](
        self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
    ) -> T:
        assert isinstance(payload, ExecutorRequest) and isinstance(toolbox, WorktreeToolbox)
        self._flight.now += 1
        self._flight.peak = max(self._flight.peak, self._flight.now)
        await asyncio.sleep(0.05)
        self._flight.now -= 1
        name = payload.instruction.goal.lower().replace(" ", "-")
        toolbox.write_file(f"{name}.md", f"{payload.instruction.goal}\n")  # one file per note: no conflicts
        if self._meter is not None:
            self._meter(Usage(input_tokens=1_000_000))
        report = ExecutorReport(summary=payload.instruction.goal, complete=True)
        assert isinstance(report, output)
        return report


class CostlyWorkers:
    def __init__(self, flight: Flight) -> None:
        self._flight = flight

    def worker(self, model: str, max_model_calls: int, meter: Meter | None = None) -> Worker:
        return CostlyWorker(meter, self._flight)


class BrokenWorkers:
    """Models whose every run fails with an error nothing expects."""

    class Broken:
        async def run[T: BaseModel](
            self, instructions: str, payload: BaseModel, output: type[T], toolbox: Toolbox | None = None
        ) -> T:
            raise ValueError("boom")

    def worker(self, model: str, max_model_calls: int, meter: Meter | None = None) -> Worker:
        return self.Broken()


class Swarms:
    @staticmethod
    def write(tmp_path: Path, budgets: dict[str, float]) -> Path:
        projects: list[dict[str, str]] = []
        for name, usd in budgets.items():
            config: dict[str, Any] = {
                "project": name,
                "domain": "software",
                "north_star": f"Notes for {name}",
                "levels": ["raw", "experimental"],
                "sandbox": {"kind": "local"},
                "models": {
                    "coder": {"model": "ollama:unused", "usd_per_million": {"input": 1.0, "output": 0.0}}
                },
                "checks": {"notes": {"command": [sys.executable, "-c", "pass"]}},
                "teams": [{"use": "executor", "watches": "raw", "model": "coder", "checks": ["notes"]}],
                "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["notes"]}],
                "budgets": {"usd_per_day": usd},
            }
            (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(config))
            projects.append({"config": f"{name}.yaml", "root": f"runs/{name}"})
        swarm: dict[str, Any] = {"scheduler": {"max_model_calls": 4}, "projects": projects}
        (tmp_path / "swarm.yaml").write_text(yaml.safe_dump(swarm))
        return tmp_path / "swarm.yaml"


class TestSwarm:
    async def test_two_projects_run_concurrently_within_their_budgets(self, tmp_path: Path) -> None:
        flight = Flight()

        def services(config: ProjectConfig) -> ProjectServices:
            return ProjectServices(
                workers=CostlyWorkers(flight), decisions=InMemoryDecisions(), clock=FakeClock(Samples.AT)
            )

        swarm = Swarm.open(SwarmConfig.load(Swarms.write(tmp_path, {"roomy": 100.0, "tight": 2.0})), services)
        for i in range(3):
            swarm.projects["roomy"].create_artifact(f"Roomy note {i}")
        for i in range(4):
            swarm.projects["tight"].create_artifact(f"Tight note {i}")
        runs = await swarm.run()

        assert all(run.rounds is not None for run in runs)
        roomy, tight = swarm.projects["roomy"].views(), swarm.projects["tight"].views()
        assert len(roomy.artifacts_at(Samples.EXPERIMENTAL)) == 3
        assert len(tight.artifacts_at(Samples.EXPERIMENTAL)) == 2  # the budget ran out after two
        assert len(tight.artifacts_at(Samples.RAW)) == 2  # these wait for tomorrow
        assert tight.spent_since(Samples.AT) == 2.0 and roomy.spent_since(Samples.AT) == 3.0
        budget_reports = [
            e.event
            for e in swarm.projects["tight"].board.log.read()
            if isinstance(e.event, Escalated) and e.event.artifact is None
        ]
        assert len(budget_reports) == 1 and "budget exhausted" in budget_reports[0].reason
        report = Report.render(tight)
        assert (
            report.startswith("spent: $2.00")
            and "ESCALATED (project): supervisor: budget exhausted" in report
        )
        assert flight.peak >= 2  # model runs of the two projects overlapped
        assert set(swarm.scheduler.granted) == {"roomy", "tight"}
        swarm.close()

    def test_project_names_must_be_unique(self, tmp_path: Path) -> None:
        path = Swarms.write(tmp_path, {"one": 1.0})
        config = SwarmConfig.load(path)
        twice = config.model_copy(update={"projects": config.projects * 2})
        try:
            Swarm.open(
                twice,
                lambda c: ProjectServices(workers=CostlyWorkers(Flight()), decisions=InMemoryDecisions()),
            )
        except ValueError as error:
            assert "unique" in str(error)
        else:
            raise AssertionError("a duplicate project name was accepted")

    async def test_a_failing_project_is_reported_and_the_others_finish(self, tmp_path: Path) -> None:
        def services(config: ProjectConfig) -> ProjectServices:
            workers = BrokenWorkers() if config.project == "broken" else CostlyWorkers(Flight())
            return ProjectServices(
                workers=workers, decisions=InMemoryDecisions(), clock=FakeClock(Samples.AT)
            )

        swarm = Swarm.open(
            SwarmConfig.load(Swarms.write(tmp_path, {"broken": 100.0, "healthy": 100.0})), services
        )
        swarm.projects["broken"].create_artifact("Never written")
        for i in range(2):
            swarm.projects["healthy"].create_artifact(f"Healthy note {i}")
        runs = {run.project: run for run in await swarm.run()}

        assert runs["broken"].failed and runs["broken"].status == "FAILED: ValueError: boom"
        assert not runs["healthy"].failed and runs["healthy"].rounds is not None
        assert len(swarm.projects["healthy"].views().artifacts_at(Samples.EXPERIMENTAL)) == 2
        swarm.close()
