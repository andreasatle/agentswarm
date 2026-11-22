"""A document run's data: what the user asked for, how far it got, and what it waits for."""

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.evaluation.papers import PaperSource


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunRequest(Frozen):
    """What the user sets when starting a run: the only decisions taken before the first approval."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,59}$")
    topic: str
    reader: str  # who it is for and what they know
    budget: float = Field(gt=0)  # dollars for the whole run, every stage included
    source: PaperSource = PaperSource.OPENALEX
    words: int | None = None  # None: the policy's default
    urls: tuple[str, ...] = ()  # pages to include as they are
    files: tuple[Path, ...] = ()  # the user's own documents; given, no search is made
    goal: str | None = None  # the whole brief in the user's words; None: the policy's template
    policy: Path | None = None  # a policy file instead of the default, kept for the run's later stages

    @staticmethod
    def slug(topic: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40].strip("-") or "document"


class Stage(StrEnum):
    STARTED = "started"
    SOURCES = "sources"  # found, waiting for the user's approval
    WRITING = "writing"
    CHECKING = "checking"
    PUBLISHING = "publishing"  # written and checked, waiting for the user's approval
    PUBLISHED = "published"
    STOPPED = "stopped"


class Choice(StrEnum):
    """The answers a waiting run accepts."""

    YES = "yes"  # approve (the sources, the publishing)
    STOP = "stop"  # end the run here
    RETRY = "retry"  # a failed section: try it again
    ACCEPT = "accept"  # a failed section: take it as it is
    DROP = "drop"  # a failed section: leave it out
    BUDGET = "budget"  # raise the budget (with the new amount) and go on


class Question(Frozen):
    """What a run waits for: shown to the user, answered with `agentswarm approve`."""

    title: str
    details: str
    choices: tuple[Choice, ...]


class AuditResult(Frozen):
    supported: int
    partly: int
    unsupported: int
    unruled: int

    @property
    def total(self) -> int:
        return self.supported + self.partly + self.unsupported + self.unruled

    @property
    def share(self) -> float:
        """Fully supported, of the statements ruled on."""
        ruled = self.supported + self.partly + self.unsupported
        return self.supported / ruled if ruled else 0.0

    def render(self) -> str:
        return (
            f"{self.supported} of {self.total} cited statements fully supported ({self.share:.0%}), "
            f"{self.partly} partly, {self.unsupported} not"
        )


class Answer(Frozen):
    """The user's answer to a waiting run."""

    choice: Choice
    budget: float | None = None  # with Choice.BUDGET: the new total


class DocumentRun(BaseModel):
    """The run's state, kept by LangGraph's checkpointer between the user's commands."""

    model_config = ConfigDict(extra="forbid")

    request: RunRequest
    folder: Path  # everything the run makes: sources/, project/, board/
    stage: Stage = Stage.STARTED
    document: Path | None = None
    audits: tuple[AuditResult, ...] = ()
    reviews: int = 0  # facts rounds done
    page: Path | None = None
    listing: str = ""  # the sources found, as the user is shown them
    sections: int = 0
    failed: tuple[str, ...] = ()  # sections that failed their checks, one line each
    blocked: Literal["find", "write", "audit", "review"] | None = None  # a stage the budget wouldn't fit
    need: float = 0.0  # what that stage is expected to cost
    held: str | None = None  # why that stage stopped short, when a limit refused it partway
    notes: tuple[str, ...] = ()  # what happened, for the user, in order
    answer: Answer | None = None  # the last answer given at a pause

    @property
    def sources(self) -> Path:
        return self.folder / "sources"

    @property
    def project(self) -> Path:
        return self.folder / "project"

    @property
    def board(self) -> Path:
        return self.folder / "board"


class Working:
    """A step running now: a marker in the run's folder holding the id of the process running it. Without
    it, a step in progress in another process looked like one an error had stopped (2026-11-10: `runs` said
    "stopped by an error (retry it)" while the writers worked, and a retry would have started a second)."""

    NAME: ClassVar[str] = ".running"

    def __init__(self, folder: Path) -> None:
        self._path = folder / self.NAME

    def __enter__(self) -> "Working":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc: object) -> None:
        self._path.unlink(missing_ok=True)

    def active(self) -> bool:
        """Whether the process that left the marker is alive (a marker left by a killed process is not)."""
        try:
            pid = int(self._path.read_text().strip())
        except (OSError, ValueError):
            return False
        try:
            os.kill(pid, 0)  # no signal: only whether the process exists
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # it exists, under another user
        return True


class RunView(BaseModel):
    """A run as the user sees it, for the commands and the studio: its state, its cost, what it waits for (the
    reading list as the folder holds it now), and where an error stopped it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: DocumentRun
    spent: float
    question: Question | None = None
    failure: str | None = None  # "<step>: <error>" when an error stopped it
    running: bool = False  # a step is in progress now, in some process
