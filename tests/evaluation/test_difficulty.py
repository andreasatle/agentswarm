from pathlib import Path

from agentswarm.cli import Cli
from agentswarm.evaluation.difficulty import DifficultyRatings
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.judges import Judge, TaskDifficulty
from tests.support.suites import GREETER_SUITE, GreeterSuite


class TestDifficultyRating:
    def test_rates_every_task_once_and_resumes(self, tmp_path: Path) -> None:
        tasks = len(GreeterSuite.SUITE.tasks)
        answers = [TaskDifficulty(simple=NoulAnswer(noul=0.25)) for _ in range(tasks)]
        shown: list[str] = []
        out = tmp_path / "ratings.jsonl"
        args = ["rate", str(GREETER_SUITE), "--out", str(out)]
        assert Cli(show=shown.append, rating_decider=FakeDecider(answers)).dispatch(args) == 0
        ratings = DifficultyRatings(out).done()
        assert len(ratings) == tasks
        farewell = ratings["farewell"]
        assert farewell.difficulty == 0.75 and farewell.suite == "evaluation"
        assert farewell.judge_version == Judge(TaskDifficulty).version
        again: list[str] = []
        assert Cli(show=again.append, rating_decider=FakeDecider([])).dispatch(args) == 0
        assert again[-1].endswith(f"0 rated, {tasks} already rated, 0 failed")
