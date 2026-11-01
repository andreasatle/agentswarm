"""A document run's data: what the user asked for, how far it got, and what it waits for."""

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

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
