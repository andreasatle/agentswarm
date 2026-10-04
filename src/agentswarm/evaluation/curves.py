"""Fitting model curves from comparison runs (Phase 8, routing).

Each run of a build comparison whose executor routes (`routing:` in its team config) records the task's
difficulty in a `Routed` event, with the model it ran on; the comparison scored whether that run solved the
task. Those pairs are the observations a model's curve is fitted from.
"""

from pathlib import Path

from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.core.clock import SystemClock
from agentswarm.core.domain import ProjectId, Routed
from agentswarm.evaluation.compare import ComparisonFiles, ComparisonReport, RunMeasure
from agentswarm.teams.routing import Observation
from agentswarm.workspace.lock import RootIdentity


class CurveData:
    """Observations from the results and project roots of build comparisons."""

    @classmethod
    def observations(cls, workdir: Path) -> tuple[list[Observation], int]:
        """(observations, runs skipped: no routing record, or hidden tests that couldn't run)."""
        results = ComparisonFiles.results(workdir)
        text = results.read_text()
        report = ComparisonReport.model_validate_json(text)
        observations: list[Observation] = []
        skipped = 0
        for measure in report.measures:
            observation = cls.observation(workdir, measure)
            if observation is None:
                skipped += 1
            else:
                observations.append(observation)
        return observations, skipped

    @staticmethod
    def observation(workdir: Path, measure: RunMeasure) -> Observation | None:
        if measure.solved is None:
            return None
        root = workdir / measure.setup / measure.task
        identity_path = root / RootIdentity.FILE
        identity_text = identity_path.read_text()
        identity = RootIdentity.model_validate_json(identity_text)
        project = ProjectId(identity.project)
        clock = SystemClock()
        with SqliteEventLog(root / "blackboard.sqlite", project, clock) as log:
            envelopes = log.read()
        routes = [
            e.event for e in envelopes if isinstance(e.event, Routed) and e.event.difficulty is not None
        ]
        if not routes:
            return None
        first = routes[0]  # the task's own artifact is routed first; children of a split come later
        assert first.difficulty is not None
        return Observation(model_id=first.model_id, difficulty=first.difficulty, success=measure.solved)
