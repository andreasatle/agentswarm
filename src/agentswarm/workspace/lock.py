"""What a project root is and who owns it (CODEX_AUDIT.md F04, F15).

`RootLock`: one owner process per project root.
`RootIdentity`: a root belongs to one project with one level ladder, so a different configuration can't
be pointed at it by mistake.

Claims keep teams within one process apart; nothing kept two processes from running the same project. A
process that runs a project holds an exclusive OS lock on `<root>/.agentswarm.lock` for as long as the
project is open; the OS drops it if the process dies. Commands that only read or append to the board
(status, escalations, resolve, resume) don't take it.
"""

import fcntl
import os
from pathlib import Path
from typing import IO, ClassVar, Self

from pydantic import BaseModel, ConfigDict


class ProjectLocked(Exception):
    """Another process has the project open."""


class RootLock:
    FILE = ".agentswarm.lock"

    def __init__(self, file: IO[str]) -> None:
        self._file = file

    @classmethod
    def acquire(cls, root: Path) -> Self:
        file = (root / cls.FILE).open("a+")
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            file.seek(0)
            holder = file.read().strip() or "unknown"
            file.close()
            raise ProjectLocked(f"{root} is open in another agentswarm process (pid {holder})") from None
        file.truncate(0)
        file.write(f"{os.getpid()}\n")
        file.flush()
        return cls(file)

    def close(self) -> None:
        if not self._file.closed:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()


class RootIdentity(BaseModel):
    """The structural part of a project's configuration, recorded in the root when it is first opened.

    Policy (models, thresholds, budgets, teams) may change between runs; the project name and its levels
    may not, because the event log and the level branches are built on them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    FILE: ClassVar[str] = "identity.json"

    project: str
    levels: tuple[str, ...]

    def claim(self, root: Path) -> None:
        """Record this identity in a new root, or check that an existing root has it."""
        path = root / self.FILE
        if not path.is_file():
            path.write_text(self.model_dump_json(indent=2) + "\n")  # new root, or one from before identities
            return
        recorded = type(self).model_validate_json(path.read_text())
        if recorded.project != self.project:
            raise ValueError(f"{root} belongs to project {recorded.project!r}, not {self.project!r}")
        if recorded.levels != self.levels:
            raise ValueError(
                f"{root} was created with levels {list(recorded.levels)}, not {list(self.levels)}; "
                "changing a project's levels needs a new root"
            )
