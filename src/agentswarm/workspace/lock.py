"""One owner process per project root (CODEX_AUDIT.md F04, Q1).

Claims keep teams within one process apart; nothing kept two processes from running the same project. A
process that runs a project holds an exclusive OS lock on `<root>/.agentswarm.lock` for as long as the
project is open; the OS drops it if the process dies. Commands that only read or append to the board
(status, escalations, resolve, resume) don't take it.
"""

import fcntl
import os
from pathlib import Path
from typing import IO, Self


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
