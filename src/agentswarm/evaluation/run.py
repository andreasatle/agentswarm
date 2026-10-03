"""An evaluation run: every suite task through a fresh project, then ground truth from hidden tests."""

import dataclasses
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentswarm.clock import Clock, SystemClock
from agentswarm.decisions import Decision, DecisionStore, Outcome, OutcomeSource
from agentswarm.domain import DecisionId, Escalated, Level
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.project import Project, ProjectServices
from agentswarm.sandbox import Sandboxes


class TaskResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str
    level: Level  # where the task's artifact ended up
    decisions: tuple[DecisionId, ...]  # the gate decisions made for it, oldest first
    hidden_tests_passed: tuple[bool, ...]  # per decision: did the judged commit pass the hidden tests?
    note: str = ""  # why there is no decision, if there is none (e.g. the executor escalated)


class EvaluationRun:
    """Runs a suite. All tasks share one decision log, so one calibration report covers the whole run.

    Each task gets its own project under `workdir/<task id>`. A workdir that already holds a task is
    refused rather than reused, so results never mix two runs.
    """

    MAX_ROUNDS = 12

    def __init__(
        self,
        suite: TaskSuite,
        workdir: Path,
        decisions: DecisionStore,
        services_for: Callable[[EvalTask], ProjectServices] | None = None,  # tests inject fakes
        clock: Clock | None = None,
    ) -> None:
        self._suite = suite
        self._workdir = workdir
        self._decisions = decisions
        self._services_for = services_for or self.real_services
        self._clock = clock or SystemClock()
        self._config = suite.project_config()
        self._hidden = HiddenTests(
            suite.hidden_tests_command, suite.hidden_tests_timeout_s, Sandboxes.build(self._config.sandbox)
        )

    @staticmethod
    def real_services(task: EvalTask) -> ProjectServices:
        """The real collaborators: configured models through LangChain, and Jev."""
        return ProjectServices()

    async def run(self) -> list[TaskResult]:
        return [await self.run_task(task) for task in self._suite.tasks]

    async def run_task(self, task: EvalTask) -> TaskResult:
        root = self._workdir / task.id
        if root.exists():
            raise FileExistsError(f"{root} exists; use a new workdir for a new run")
        services = dataclasses.replace(self._services_for(task), decisions=self._decisions, clock=self._clock)
        project = Project.open(self._config, root, services)
        already = len(self._decisions.all())
        try:
            artifact = project.create_artifact(task.task)
            note = ""
            try:
                await project.run_until_idle(self.MAX_ROUNDS)
            except RuntimeError as error:  # still busy: record it, the other tasks still run
                note = str(error)
            mine = self._decisions.all()[already:]  # tasks run one at a time, so these are this task's
            passed = tuple(self._hidden_outcome(project, task, d) for d in mine)
            views = project.views()
            if not mine and not note:
                escalations = [e.reason for e in views.history(artifact.id) if isinstance(e, Escalated)]
                note = escalations[-1] if escalations else "no gate decision"
            return TaskResult(
                task=task.id,
                level=views.artifact(artifact.id).level,
                decisions=tuple(d.id for d in mine),
                hidden_tests_passed=passed,
                note=note,
            )
        finally:
            project.close()

    def _hidden_outcome(self, project: Project, task: EvalTask, decision: Decision) -> bool:
        result = self._hidden.run(
            project.repository, decision.commit, task.hidden_tests, f"{task.id}-{decision.id}"
        )
        self._decisions.add_outcome(
            Outcome(
                decision=decision.id,
                field="complete",
                truth=result.passed,
                source=OutcomeSource.HIDDEN_TESTS,
                detail=result.output,
                at=self._clock.now(),
            )
        )
        return result.passed
