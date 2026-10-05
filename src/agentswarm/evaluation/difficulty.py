"""Difficulty ratings: Jev scores every task of some suites, before any model tries them (Phase 8, step 10).

The question is the router's own (`TaskDifficulty`: "is this a simple task…?"), asked about the task text
alone, as the router asks it before any work exists. The difficulty is 1 - p(simple). Ratings are written to
a JSON Lines file as they come, each with the judge's version and Jev's model, so a rerun skips what is done
and ratings from different wordings are never mixed silently.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import ProjectConfig
from agentswarm.evaluation.suite import TaskSuite
from agentswarm.judging.deciders import Decider, DeciderError
from agentswarm.judging.judges import Judge, JudgeState, TaskDifficulty


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskRating(Frozen):
    task: str  # task ids are unique across the suites rated together
    suite: str  # the suite file's folder name, e.g. "humanevalplus"
    difficulty: float  # 1 - p(simple)
    judge_version: str
    jev_model: str


class DifficultyRatings:
    """The ratings file: one rating per line, appended as they come."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def done(self) -> dict[str, TaskRating]:
        if not self.path.is_file():
            return {}
        ratings = [
            TaskRating.model_validate_json(line) for line in self.path.read_text().splitlines() if line
        ]
        return {r.task: r for r in ratings}

    def append(self, rating: TaskRating) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as file:
            file.write(rating.model_dump_json() + "\n")


class DifficultyRater:
    """Asks the decider how simple each task is, skipping tasks already rated with the same wording."""

    def __init__(self, decider: Decider, ratings: DifficultyRatings) -> None:
        self._decider = decider
        self._ratings = ratings
        self._version = Judge(TaskDifficulty).version

    def rate(self, suite_path: Path, report: Callable[[str], None] | None = None) -> tuple[int, int, int]:
        """(rated now, already rated, failed) for one suite."""
        suite = TaskSuite.load(suite_path)
        north_star = ProjectConfig.load(suite.project).north_star
        done = self._ratings.done()
        rated = skipped = failed = 0
        for task in suite.tasks:
            earlier = done.get(task.id)
            if earlier is not None and earlier.judge_version == self._version:
                skipped += 1
                continue
            state = JudgeState(north_star=north_star, task=task.task, must_fix=(), diff="")
            try:
                answered = self._decider.decide(state, TaskDifficulty)
            except DeciderError as error:
                failed += 1
                if report is not None:
                    report(f"{task.id}: no rating ({error})")
                continue
            difficulty = round(1.0 - answered.answers.simple.noul, 4)
            rating = TaskRating(
                task=task.id,
                suite=suite_path.parent.name,
                difficulty=difficulty,
                judge_version=self._version,
                jev_model=answered.model,
            )
            self._ratings.append(rating)
            rated += 1
            if report is not None:
                report(f"{task.id:<16} difficulty {difficulty:.3f}")
        return rated, skipped, failed
