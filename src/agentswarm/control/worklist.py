"""The worklist: a test suite's failing tests become work items (ROADMAP.md, stage 1).

When a project is built against a large external test suite (a spec's conformance examples), the suite
decides how the work is split, not a model guessing a plan: each test file with failing tests on the first
shared level becomes an item at the lowest level, "make the failing tests in this file pass". Plain code, like
the finding router: it keeps at most `max_open` items in progress, gives each file a limited number of items,
and stops when nothing fails or every remaining file has used its items. The ratchet gate makes sure each
merged item moves the suite forward without breaking anything.
"""

import fnmatch
from collections import Counter

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.board.blackboard import Blackboard
from agentswarm.core.config import LevelLadder, WorklistConfig
from agentswarm.core.domain import TeamName
from agentswarm.gating.checks import RatchetCheck
from agentswarm.workspace.git import Repository


class Worklist:
    NAME = TeamName("worklist")

    def __init__(
        self,
        board: Blackboard,
        artifacts: ArtifactFactory,
        repository: Repository,
        ladder: LevelLadder,
        check: RatchetCheck,
        config: WorklistConfig,
    ) -> None:
        self._board = board
        self._artifacts = artifacts
        self._repository = repository
        self._ladder = ladder
        self._check = check
        self._config = config

    def step(self) -> int:
        """Add items while fewer than `max_open` are in progress. Returns how many it added."""
        views = self._board.current()
        if views.paused:
            return 0  # no new items while the project waits (e.g. for a provider)
        mine = [a for a in views.artifacts() if a.created_by == self.NAME]
        in_progress = [a for a in mine if a.level == self._ladder.lowest and views.active(a.id)]
        if len(in_progress) >= self._config.max_open:
            return 0
        tip = self._repository.tip(self._ladder.base)
        outcomes = self._check.outcomes_at(self._repository.path, tip)
        if outcomes is None:
            return 0  # the suite didn't run; try again next step
        failing = Counter(node.split("::", 1)[0] for node in outcomes.failed)
        total = Counter(node.split("::", 1)[0] for node in outcomes.passed | outcomes.failed)
        used = Counter(self.file_of(a.task) for a in mine)
        busy = {self.file_of(a.task) for a in in_progress}
        added = 0
        for path in sorted(failing):  # in file order: a spec's sections come in the order they build on
            if len(in_progress) + added >= self._config.max_open:
                break
            if path in busy or used[path] >= self._config.attempts_per_file:
                continue
            if self._config.only and not any(fnmatch.fnmatch(path, p) for p in self._config.only):
                continue
            ids = sorted(n.split("::", 1)[1] for n in outcomes.failed if n.startswith(f"{path}::"))
            self._artifacts.create(self.task(path, failing[path], total[path], ids), self.NAME)
            added += 1
        return added

    PREFIX = "Make the failing tests in "

    def task(self, path: str, failing: int, total: int, ids: list[str]) -> str:
        shown = ", ".join(ids[: self._config.shown])
        return (
            f"{self.PREFIX}{path} pass: {failing} of its {total} tests fail now (first: {shown}). "
            "Use run_tests on that file to see why. Tests that pass now must keep passing, and the tests "
            "themselves can't be changed. Fix as many as you can; a change that makes some pass and breaks "
            "none is progress."
        )

    @classmethod
    def file_of(cls, task: str) -> str:
        """The test file an item's task names."""
        return task.removeprefix(cls.PREFIX).split(" pass:", 1)[0] if task.startswith(cls.PREFIX) else ""
