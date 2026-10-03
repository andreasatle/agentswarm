"""An evaluation run: every suite task through fresh projects, then ground truth for each judgement.

For each task and each variant (see `faults.Variant`) the run opens a fresh project, lets the executor
work and the gate judge in shadow mode, and then attaches outcomes to every decision it made:

    complete        hidden acceptance tests on the judged commit            (all variants)
    tests_relevant  mutation testing of the change's own tests              (all variants, when it can)
    in_scope,       known by construction                                    (scripted variants)
    tests_relevant
"""

import dataclasses
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentswarm.core.clock import Clock, SystemClock
from agentswarm.core.domain import DecisionId, Escalated, Level
from agentswarm.evaluation.faults import ScriptedExecutor, Variant
from agentswarm.evaluation.mutation import MutationTesting
from agentswarm.evaluation.suite import EvalTask, HiddenTests, TaskSuite
from agentswarm.judging.decisions import Decision, DecisionStore, Outcome, OutcomeSource
from agentswarm.project import Project, ProjectServices
from agentswarm.teams.catalog import TeamCatalog
from agentswarm.workspace.sandbox import Sandboxes


class TaskResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str
    variant: Variant
    level: Level  # where the task's artifact ended up
    decisions: tuple[DecisionId, ...]  # the gate decisions made for it, oldest first
    hidden_tests_passed: tuple[bool, ...]  # per decision
    mutation_scores: tuple[float | None, ...]  # per decision; None when mutation testing had no answer
    note: str = ""  # why there is no decision, if there is none (e.g. the executor escalated)


class EvaluationRun:
    """Runs a suite. All runs share one decision log, so one calibration report covers them all.

    Each (task, variant) gets its own project under `workdir/<task id>/<variant>`. A directory that
    already exists is refused rather than reused, so results never mix two runs.
    """

    MAX_ROUNDS = 12

    def __init__(
        self,
        suite: TaskSuite,
        workdir: Path,
        decisions: DecisionStore,
        services_for: Callable[[EvalTask], ProjectServices] | None = None,  # tests inject fakes
        variants: Sequence[Variant] = tuple(Variant),
        clock: Clock | None = None,
    ) -> None:
        if any(v.scripted for v in variants):
            missing = [t.id for t in suite.tasks if not t.reference]
            if missing:
                raise ValueError(f"scripted variants need reference solutions; tasks without: {missing}")
        self._suite = suite
        self._workdir = workdir
        self._decisions = decisions
        self._services_for = services_for or self.real_services
        self._variants = tuple(variants)
        self._clock = clock or SystemClock()
        self._config = suite.project_config()
        sandbox = Sandboxes.build(self._config.sandbox)
        self._hidden = HiddenTests(suite.test_command, suite.test_timeout_s, sandbox)
        self._mutation = MutationTesting(suite.test_command, suite.test_timeout_s, sandbox)

    @staticmethod
    def real_services(task: EvalTask) -> ProjectServices:
        """The real collaborators: configured models through LangChain, and Jev."""
        return ProjectServices()

    async def run(self) -> list[TaskResult]:
        return [
            await self.run_task(task, variant) for task in self._suite.tasks for variant in self._variants
        ]

    async def run_task(self, task: EvalTask, variant: Variant) -> TaskResult:
        root = self._workdir / task.id / variant
        if root.exists():
            raise FileExistsError(f"{root} exists; use a new workdir for a new run")
        services = dataclasses.replace(self._services_for(task), decisions=self._decisions, clock=self._clock)
        if variant.scripted:
            services = dataclasses.replace(services, teams=self._scripted_catalog(task, variant))
        project = Project.open(self._config, root, services)
        already = len(self._decisions.all())
        try:
            artifact = project.create_artifact(task.task)
            note = ""
            try:
                await project.run_until_idle(self.MAX_ROUNDS)
            except RuntimeError as error:  # still busy: record it, the other runs still go ahead
                note = str(error)
            mine = self._decisions.all()[already:]  # runs go one at a time, so these are this run's
            passed = tuple(self._hidden_outcome(project, task, variant, d) for d in mine)
            scores = tuple(self._mutation_outcome(project, task, variant, d) for d in mine)
            for decision in mine:
                for field, truth in variant.known_truths().items():
                    self._record(decision, field, truth, OutcomeSource.PLANTED, f"variant {variant}")
            views = project.views()
            if not mine and not note:
                escalations = [e.reason for e in views.history(artifact.id) if isinstance(e, Escalated)]
                note = escalations[-1] if escalations else "no gate decision"
            return TaskResult(
                task=task.id,
                variant=variant,
                level=views.artifact(artifact.id).level,
                decisions=tuple(d.id for d in mine),
                hidden_tests_passed=passed,
                mutation_scores=scores,
                note=note,
            )
        finally:
            project.close()

    @staticmethod
    def _scripted_catalog(task: EvalTask, variant: Variant) -> TeamCatalog:
        """A catalog whose "executor" writes the reference solution in the given variant."""
        catalog = TeamCatalog()
        catalog.register("executor", lambda team, resources: ScriptedExecutor(team.watches, task, variant))
        return catalog

    def _hidden_outcome(self, project: Project, task: EvalTask, variant: Variant, decision: Decision) -> bool:
        name = f"{task.id}-{variant}-{decision.id}"
        result = self._hidden.run(project.repository, decision.commit, task.hidden_tests, name)
        self._record(decision, "complete", result.passed, OutcomeSource.HIDDEN_TESTS, result.output)
        return result.passed

    def _mutation_outcome(
        self, project: Project, task: EvalTask, variant: Variant, decision: Decision
    ) -> float | None:
        name = f"{task.id}-{variant}-{decision.id}"
        # Against the base recorded when judging: by now the level may already contain the change.
        report = self._mutation.run(project.repository, decision.commit, decision.base, name)
        if report is None:
            return None
        tests = list(report.test_files) or "no new tests"
        detail = f"{report.killed}/{report.mutants} mutants killed by {tests}"
        self._record(decision, "tests_relevant", self._mutation.truth(report), OutcomeSource.MUTATION, detail)
        return report.score

    def _record(
        self, decision: Decision, field: str, truth: bool, source: OutcomeSource, detail: str
    ) -> None:
        self._decisions.add_outcome(
            Outcome(
                decision=decision.id,
                field=field,
                truth=truth,
                source=source,
                detail=detail,
                at=self._clock.now(),
            )
        )
