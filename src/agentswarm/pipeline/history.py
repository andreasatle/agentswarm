"""A run's history: every pause it reached, with the run's files as they were there, so the user can read any
of them and continue from any of them (the user, 2026-11-24: "go back and forth ... add a reference and rerun
from there ... easy to navigate"). Continuing from an earlier pause starts a new version of the run beside the
old one, which is kept: LangGraph forks its checkpoints, and the run's files are put back as that pause left
them. A first step toward goal 16 (work set off by change): a change redoes everything after the pause it is
made at, not yet only what it touches."""

import shutil
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.evaluation.documents import DocumentImport
from agentswarm.pipeline.run import Answer, Choice, DocumentRun, Question, Stage


class Moment(BaseModel):
    """One pause the run reached (or its end), as the user sees it in the history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # the checkpoint's id
    version: int  # 1, then one more each time the user continued from an earlier moment
    title: str  # the pause's question, or how the run ended
    when: datetime
    run: DocumentRun  # the run's state there
    answer: Answer | None = None  # what the user answered there, in this version (None: not yet, or the end)
    current: bool = False  # where the run is now
    kept: bool = False  # its files are saved, so it can be read and continued from
    pause: bool = True  # False for the end (published or stopped): read only
    sources: int | None = None  # documents in its sources folder, when kept

    @property
    def facts(self) -> str:
        """What the user decided on at this moment: the questions, the sources, or the document's checks."""
        run = self.run
        if run.stage is Stage.SCOPE:
            return f"{len(run.questions)} questions"
        if run.stage is Stage.SOURCES:
            if self.sources is None:
                return "the sources found"
            return f"{self.sources} source{'s' if self.sources != 1 else ''}"
        parts = [f"citations {run.audits[-1].share:.0%}"] if run.audits else []
        if run.revisions:
            parts.append(f"{run.revisions} round{'s' if run.revisions != 1 else ''} of your changes")
        return " · ".join(parts)

    @property
    def answered(self) -> str | None:
        """What the user did at this moment, in plain words."""
        if self.answer is None:
            return None
        a = self.answer
        if a.choice is Choice.YES:
            return "published" if self.title.startswith("Approve publishing") else "approved"
        if a.choice is Choice.REVISE:
            asked = len(a.add)
            return "asked for changes" + (f" ({asked} of your own)" if asked else "")
        if a.choice is Choice.BUDGET and a.budget is not None:
            return f"raised the budget to ${a.budget:.2f}"
        return {
            Choice.STOP: "stopped the run",
            Choice.RETRY: "tried again",
            Choice.ACCEPT: "accepted them",
            Choice.DROP: "left them out",
        }.get(a.choice, str(a.choice))


class RunFiles:
    """The files a run's steps make, saved at each moment and put back when the user continues from one.
    Everything else in the run's folder (the user's own notes, uploads) is left alone."""

    KEPT: ClassVar[tuple[str, ...]] = (
        "sources",
        "removed-sources",
        "project",
        "board",
        "page.html",
        "document.md",
    )
    FOLDER: ClassVar[str] = "history"

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def at(self, moment: str) -> Path:
        return self._folder / self.FOLDER / moment

    def has(self, moment: str) -> bool:
        return self.at(moment).is_dir()

    def save(self, moment: str) -> None:
        """The run's files as they are now, under the moment's name; once (a moment's files never change)."""
        if self.has(moment):
            return
        partial = self.at(moment + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        for name in self.KEPT:
            self.copy(self._folder / name, partial / name)
        partial.rename(self.at(moment))

    def restore(self, moment: str) -> None:
        """The run's files put back as the moment left them; what it didn't have is removed."""
        if not self.has(moment):
            raise ValueError(f"the files of moment {moment} weren't kept")
        for name in self.KEPT:
            self.remove(self._folder / name)
            self.copy(self.at(moment) / name, self._folder / name)

    def locate(self, moment: str, path: Path) -> Path:
        """Where a path of the run's (its document, say) is in the moment's saved files."""
        return self.at(moment) / path.relative_to(self._folder)

    @staticmethod
    def copy(source: Path, target: Path) -> None:
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, target)

    @staticmethod
    def remove(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()


class Timeline(BaseModel):
    """The history as the user is shown it, never as a tree (the user, 2026-11-24: branching chats are hard
    to follow): one straight line, how the version the run is on got here, and the other versions beside it,
    one line each, by where they ended."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: tuple[Moment, ...]  # oldest first, ending where the run is now
    others: tuple[Moment, ...]  # each other version's last moment, the newest version first

    @property
    def version(self) -> int:
        return self.path[-1].version if self.path else 1


class Checkpoint(BaseModel):
    """What the history needs of one of LangGraph's checkpoints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    parent: str | None
    when: datetime
    run: DocumentRun | None  # None before the first step
    question: Question | None  # the pause it waits at
    end: bool  # nothing left to run


class History:
    """The moments of a run from its checkpoints, every version of it, oldest first."""

    def __init__(self, checkpoints: list[Checkpoint], head: str | None, files: RunFiles) -> None:
        self._checkpoints = sorted(checkpoints, key=lambda c: c.when)
        self._head = head
        self._files = files

    def versions(self) -> dict[str, int]:
        """A checkpoint's version: its parent's, unless its parent had an earlier child (it was continued from
        there, so a new version starts)."""
        version: dict[str, int] = {}
        children: dict[str, int] = {}
        count = 1
        for c in self._checkpoints:
            if c.parent is None or c.parent not in version:
                version[c.id] = 1
            elif children.get(c.parent):
                count += 1
                version[c.id] = count
            else:
                version[c.id] = version[c.parent]
            if c.parent is not None:
                children[c.parent] = children.get(c.parent, 0) + 1
        return version

    def moments(self) -> tuple[Moment, ...]:
        version = self.versions()
        first_child: dict[str, Checkpoint] = {}
        for c in self._checkpoints:
            if c.parent is not None and c.parent not in first_child:
                first_child[c.parent] = c
        moments: list[Moment] = []
        for c in self._checkpoints:
            if c.run is None or (c.question is None and not c.end):
                continue
            child = first_child.get(c.id)
            answer = child.run.answer if c.question is not None and child and child.run else None
            kept = self._files.has(c.id)
            title = c.question.title if c.question is not None else self.ending(c.run)
            moments.append(
                Moment(
                    id=c.id,
                    version=version[c.id],
                    title=title,
                    when=c.when,
                    run=c.run,
                    answer=answer,
                    current=c.id == self._head,
                    kept=kept,
                    pause=c.question is not None,
                    sources=len(DocumentImport.files([self._files.at(c.id) / "sources"])) if kept else None,
                )
            )
        return tuple(moments)

    def timeline(self) -> Timeline:
        moments = self.moments()
        parents = {c.id: c.parent for c in self._checkpoints}
        line: set[str] = set()
        at = self._head
        while at is not None:
            line.add(at)
            at = parents.get(at)
        on = [m for m in moments if m.id in line]
        # a pause continued from is asked again as a new checkpoint; on the line, the later stands for both
        again = {parents[m.id] for m in on if m.pause}
        path = tuple(m for m in on if not (m.pause and m.id in again))
        here = path[-1].version if path else 1
        last: dict[int, Moment] = {}
        for m in moments:
            if m.version != here and m.id not in line:
                last[m.version] = m
        return Timeline(path=path, others=tuple(last[v] for v in sorted(last, reverse=True)))

    @staticmethod
    def ending(run: DocumentRun) -> str:
        return {Stage.PUBLISHED: "Published", Stage.STOPPED: "Stopped"}.get(run.stage, "Ended")
