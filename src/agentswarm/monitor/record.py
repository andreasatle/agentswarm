"""The run record: every verdict on a board as one row, tied to the models and prompts that made what was
judged and to the ones that judged it (docs/design/run-record.md). Read only, from the event log alone.

The dataset that learning from runs (prompt improvement from failed steps) and the competitiveness study
need: which model, with which prompt, produced a piece of work, which check judged it, and the verdict.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentswarm.board.eventlog import SqliteEventLog
from agentswarm.core.clock import SystemClock
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import (
    ArtifactId,
    Attacked,
    Checked,
    Envelope,
    OutcomeRecorded,
    Perspective,
    ProjectId,
    PromptId,
    Routed,
    SectionAssigned,
    Spent,
)
from agentswarm.workspace.lock import RootIdentity

type Choice = Callable[[Spent], bool]  # which spend a verdict is tied to


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Maker(Frozen):
    """A model with a prompt, as the spend of one role recorded it."""

    by: str  # the role whose spend it was: a team, "claim-checker", "referee", ...
    model: str  # "<provider>:<model>", or the project's name for it on boards from before model ids
    prompt: PromptId | None  # None on boards from before prompts were recorded


class CheckKind(StrEnum):
    """What judged: a model's check of a cited statement, the referee's ruling on a flag, or a routed call's
    outcome as settled by later events (the gate, a rejection, a repair)."""

    CLAIM = "claim"
    RULING = "ruling"
    OUTCOME = "outcome"


class Row(Frozen):
    """One verdict: what was judged, by what, and what made it."""

    project: str
    seq: int  # the verdict's position in the log
    at: datetime
    kind: CheckKind
    check: str  # "claim-checker", "citation-audit", "referee", or "outcome:<call kind>"
    verdict: str  # supported, partly, unsupported, unruled; confirmed, rejected; success, failure
    subject: str  # the statement, or the artifact
    file: str | None = None  # the section holding the statement
    commit: str | None = None  # the version judged, when known
    reason: str = ""
    checkers: tuple[Maker, ...] = ()  # the models behind the check; empty when plain code settled it
    producers: tuple[Maker, ...] = ()  # the calls that made what was judged, up to the verdict


class RunRecord:
    """Joins a board's verdicts to its spend. A role's models and prompts are those its spend recorded before
    the verdict; a section's makers are the spend on the artifacts that wrote it (its writing task and the
    revisions the reviews made of it). Several makers in a row mean the work had several (a revision by
    another model, or a prompt changed between runs on the same board)."""

    def __init__(
        self,
        project: str,
        envelopes: Sequence[Envelope],
        model_ids: Mapping[str, str] | None = None,  # model name -> "<provider>:<model>", from a config
    ) -> None:
        self._project = project
        self._envelopes = list(envelopes)
        # The model of spend from before model ids were recorded: by the names routing recorded, else by the
        # configuration's (which may have changed since the run)
        self._ids = dict(model_ids or {}) | {
            e.event.model: e.event.model_id for e in self._envelopes if isinstance(e.event, Routed)
        }

    @classmethod
    def of(cls, root: Path, config: ProjectConfig | None = None) -> "RunRecord":
        """The record of the board in `root` (a project root, or a document run's `board` folder); without a
        `config`, a document run's own (`project/project.yaml` beside its board) names its models."""
        identity = RootIdentity.model_validate_json((root / RootIdentity.FILE).read_text())
        beside = root.parent / "project" / "project.yaml"
        if config is None and beside.is_file():
            config = ProjectConfig.load(beside)
        ids = {name: model.model for name, model in config.models.items()} if config is not None else {}
        with SqliteEventLog(root / "blackboard.sqlite", ProjectId(identity.project), SystemClock()) as log:
            return cls(identity.project, log.read(), ids)

    def rows(self) -> Iterator[Row]:
        for envelope in self._envelopes:
            event = envelope.event
            match event:
                case Checked():
                    yield self.checked(envelope, event)
                case Attacked() if event.focus is Perspective.CLAIMS:
                    yield from self.rulings(envelope, event)
                case OutcomeRecorded():
                    yield self.outcome(envelope, event)
                case _:
                    pass

    def checked(self, envelope: Envelope, event: Checked) -> Row:
        """A model's verdict on a cited statement: the claim checker's, or the citation audit's."""
        return self.row(
            envelope,
            CheckKind.CLAIM,
            event.by,
            str(event.support or "unruled"),
            event.statement,
            file=event.file,
            commit=event.commit,
            reason=event.reason,
            checkers=self.makers(envelope.seq, lambda s: s.by == event.by),
            producers=self.writers(envelope.seq, event.file),
        )

    def outcome(self, envelope: Envelope, event: OutcomeRecorded) -> Row:
        """A routed call's outcome; its makers are the team's spend on the artifact with that model."""
        return self.row(
            envelope,
            CheckKind.OUTCOME,
            f"outcome:{event.call_kind}",
            "success" if event.success else "failure",
            event.artifact,
            producers=self.makers(
                envelope.seq,
                lambda s: (
                    s.artifact == event.artifact and s.by == event.team and self.model_id(s) == event.model_id
                ),
            ),
        )

    def rulings(self, envelope: Envelope, event: Attacked) -> Iterator[Row]:
        """The referee's ruling on each flag of a claims review: upheld (confirmed) or not (rejected)."""
        referee = self.makers(envelope.seq, lambda s: s.by == "referee")
        writers = self.writers(envelope.seq, event.section)
        for finding in event.findings:
            yield self.row(
                envelope,
                CheckKind.RULING,
                "referee",
                "confirmed",
                finding.quotes[0] if finding.quotes else finding.claim,
                file=event.section,
                commit=event.commit,
                reason=finding.claim,
                checkers=referee,
                producers=writers,
            )
        for rejected in event.rejected:
            yield self.row(
                envelope,
                CheckKind.RULING,
                "referee",
                "rejected",
                rejected.statement or rejected.claim,
                file=event.section,
                commit=event.commit,
                reason=rejected.reason,
                checkers=referee,
                producers=writers,
            )

    def row(
        self, envelope: Envelope, kind: CheckKind, check: str, verdict: str, subject: str, **rest: object
    ) -> Row:
        return Row.model_validate(
            {
                "project": self._project,
                "seq": envelope.seq,
                "at": envelope.at,
                "kind": kind,
                "check": check,
                "verdict": verdict,
                "subject": subject,
                **rest,
            }
        )

    JEV = "jev:"  # spend on Jev's questions about an artifact (routing, sizing): not its making

    def writers(self, before: int, file: str) -> tuple[Maker, ...]:
        """The makers of a section file: the spend on its writing task and on the revisions made of it."""
        artifacts: set[ArtifactId] = set()
        for envelope in self.until(before):
            event = envelope.event
            if isinstance(event, SectionAssigned) and event.path == file:
                artifacts.add(event.artifact)
            elif isinstance(event, Attacked) and event.section == file:
                artifacts.update(event.tasks)
        return self.makers(before, lambda s: s.artifact in artifacts and not s.by.startswith(self.JEV))

    def model_id(self, spent: Spent) -> str:
        return spent.model_id or self._ids.get(spent.model, spent.model)

    def until(self, before: int) -> Iterator[Envelope]:
        return (e for e in self._envelopes if e.seq < before)

    def makers(self, before: int, chosen: Choice) -> tuple[Maker, ...]:
        """The distinct models and prompts of the spend before position `before` that `chosen` picks."""
        found: dict[Maker, None] = {}
        for envelope in self.until(before):
            event = envelope.event
            if isinstance(event, Spent) and chosen(event):
                found[Maker(by=event.by, model=self.model_id(event), prompt=event.prompt)] = None
        return tuple(found)
