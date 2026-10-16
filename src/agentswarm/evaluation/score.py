"""The scoreboard: how far a project's first shared level is through a test suite (ROADMAP.md, stage 1).

Progress on a project built against an external test suite is a number: the share of its tests that pass.
The level's tip is copied without git metadata (`git archive`), extra test folders no agent has seen (held-out
examples) are copied in beside its own tests, and the suite runs in the project's sandbox, like the ratchet.
"""

import asyncio
import io
import shutil
import tarfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import BoardViews
from agentswarm.core.config import ProjectConfig, ScoreConfig
from agentswarm.core.domain import Scored, Tally
from agentswarm.gating.ratchet import Outcomes
from agentswarm.workspace.git import Git
from agentswarm.workspace.sandbox import Sandbox


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FileScore(Frozen):
    path: str
    passed: int
    total: int


class Score(Frozen):
    commit: str
    files: tuple[FileScore, ...]

    def total(self, prefix: str = "") -> tuple[int, int]:
        mine = [f for f in self.files if f.path.startswith(prefix)]
        return sum(f.passed for f in mine), sum(f.total for f in mine)

    def render(self, groups: Sequence[tuple[str, str]]) -> str:
        """Per file, then a total per (label, path prefix) group."""
        lines = [f"commit {self.commit[:10]}"]
        lines += [f"  {f.passed:>4}/{f.total:<4} {f.path}" for f in self.files]
        for label, prefix in groups:
            passed, total = self.total(prefix)
            share = f"{passed / total:.0%}" if total else "-"
            lines.append(f"{label}: {passed}/{total} ({share})")
        return "\n".join(lines)


class Scoreboard:
    EXTRA = "tests/_scored"  # where extra test folders go in the copy

    def __init__(self, repository: Path, sandbox: Sandbox, command: Sequence[str], timeout_s: float = 600.0):
        self._repository = repository
        self._sandbox = sandbox
        self._command = tuple(command)
        self._timeout_s = timeout_s

    def score(self, ref: str, extra: Sequence[Path] = ()) -> Score:
        git = Git()
        commit = git.run(self._repository, "rev-parse", ref)
        copy = self._repository.parent / f"_score-{commit[:12]}"
        shutil.rmtree(copy, ignore_errors=True)
        copy.mkdir(parents=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(git.archive(self._repository, commit))) as tar:
                tar.extractall(copy, filter="data")
            for folder in extra:
                shutil.copytree(folder, copy / self.EXTRA / folder.name)
            result = self._sandbox.run((*self._command, "-rA", "--tb=no"), copy, self._timeout_s)
        finally:
            shutil.rmtree(copy, ignore_errors=True)
        outcomes = Outcomes.of(result)
        passed = Counter(n.split("::", 1)[0] for n in outcomes.passed)
        total = Counter(n.split("::", 1)[0] for n in outcomes.passed | outcomes.failed)
        files = tuple(FileScore(path=p, passed=passed[p], total=total[p]) for p in sorted(total))
        return Score(commit=commit, files=files)


class Measure(Protocol):
    async def measure(self, views: BoardViews) -> Scored:
        """The first shared level against the definition of done, as numbers (not yet on the board)."""
        ...


class Progress:
    """The definition of done as numbers, measured on the first shared level and recorded on the board.

    A run is scored at its start and its end, so its report says what it bought: examples gained, seen and
    held out, and the cost per example (docs/design/best-practice-review.md, fix 8).
    """

    def __init__(self, config: ProjectConfig, score: ScoreConfig, repository: Path, sandbox: Sandbox) -> None:
        check = config.checks[score.check]
        self._score = score
        self._level = config.levels.base
        self._board = Scoreboard(repository, sandbox, check.command, check.timeout_s)

    async def measure(self, views: BoardViews) -> Scored:
        held_out = [p.expanduser().resolve() for p in self._score.held_out]
        score = await asyncio.to_thread(self._board.score, self._level, held_out)
        hidden = [score.total(f"{Scoreboard.EXTRA}/{p.name}/") for p in held_out]
        return Scored(
            level=self._level,
            commit=score.commit,
            seen=Tally(passed=score.total(self._score.seen)[0], total=score.total(self._score.seen)[1]),
            held_out=Tally(passed=sum(p for p, _ in hidden), total=sum(t for _, t in hidden))
            if held_out
            else None,
            spent_usd=views.spent()[0],
        )

    @staticmethod
    def tally(t: Tally) -> str:
        share = f" ({t.share:.0%})" if t.share is not None else ""
        return f"{t.passed}/{t.total}{share}"

    @classmethod
    def render(cls, before: Scored, after: Scored) -> str:
        """What a run bought: the change in each tally, and its cost per example gained."""
        cost = after.spent_usd - before.spent_usd
        lines = [f"progress on {after.level} (cost ${cost:.2f}):"]
        if before.seen is not None and after.seen is not None:
            gained = after.seen.passed - before.seen.passed
            lines.append(f"  seen tests: {cls.tally(before.seen)} -> {cls.tally(after.seen)} ({gained:+d})")
        if before.held_out is not None and after.held_out is not None:
            held = after.held_out.passed - before.held_out.passed
            lines.append(
                f"  held out:   {cls.tally(before.held_out)} -> {cls.tally(after.held_out)} ({held:+d})"
            )
        units = [
            (a.passed - b.passed, label)
            for b, a, label in (
                (before.seen, after.seen, "seen"),
                (before.held_out, after.held_out, "held-out"),
            )
            if b is not None and a is not None
        ]
        gained, label = units[0] if units else (0, "seen")
        per = (
            f"${cost / gained:.2f} per {label} example gained" if gained > 0 else f"no {label} example gained"
        )
        lines.append(f"  {per}")
        return "\n".join(lines)
