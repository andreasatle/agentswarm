"""Plain folders of each level, for people (the tutorial, a quick look at what a project produced).

Levels are git branches and teams work in temporary worktrees, which is right for the machinery and awkward
for a person: seeing what passed a gate took git commands. `LevelFolders` writes the files at each level's
tip into `<root>/levels/<level>/`, without git metadata, replacing what was there: a snapshot to open in an
editor or file browser. Nothing reads them back; the branches stay the source of truth.
"""

import io
import shutil
import tarfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import LevelLadder
from agentswarm.workspace.git import Git


class LevelSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: str
    commit: str
    files: int
    folder: Path


class LevelFolders:
    FOLDER = "levels"

    def __init__(self, repository: Path, root: Path, ladder: LevelLadder, git: Git | None = None) -> None:
        self._repository = repository
        self._out = root / self.FOLDER
        self._ladder = ladder
        self._git = git or Git()

    def write(self) -> list[LevelSnapshot]:
        """Every level's files as they are at its tip; levels without a branch yet are skipped."""
        snapshots: list[LevelSnapshot] = []
        for level in self._ladder.levels:
            attempt = self._git.attempt(
                self._repository, "rev-parse", "--verify", "--quiet", f"refs/heads/{level}"
            )
            if attempt.returncode != 0:
                continue
            commit = attempt.stdout.strip()
            folder = self._out / level
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(self._git.archive(self._repository, commit))) as tar:
                tar.extractall(folder, filter="data")
                files = sum(1 for member in tar.getmembers() if member.isfile())
            snapshots.append(LevelSnapshot(level=level, commit=commit, files=files, folder=folder))
        return snapshots

    @staticmethod
    def render(snapshots: list[LevelSnapshot]) -> str:
        lines = ["What each level holds now (plain files, refreshed after every run):"]
        lines += [f"  {s.level:<14} {s.commit[:10]}  {s.files:>4} files  {s.folder}" for s in snapshots]
        return "\n".join(lines)
