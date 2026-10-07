"""The scoreboard: how far a project's first shared level is through a test suite (ROADMAP.md, stage 1).

Progress on a project built against an external test suite is a number: the share of its tests that pass.
The level's tip is copied without git metadata (`git archive`), extra test folders no agent has seen (held-out
examples) are copied in beside its own tests, and the suite runs in the project's sandbox, like the ratchet.
"""

import io
import shutil
import tarfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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
        outcomes = Outcomes.parse(result.output)
        passed = Counter(n.split("::", 1)[0] for n in outcomes.passed)
        total = Counter(n.split("::", 1)[0] for n in outcomes.passed | outcomes.failed)
        files = tuple(FileScore(path=p, passed=passed[p], total=total[p]) for p in sorted(total))
        return Score(commit=commit, files=files)
