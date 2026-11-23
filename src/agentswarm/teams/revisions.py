"""The user's changes at the publishing pause (docs/design/outer-loop.md: inspect, then act): after reading
the finished document, the user keeps the red team's findings they agree with and writes their own
instructions, and the section writers make them in one round. What Claude did by hand until now (reading,
then telling the writers), done by the framework.

The user's word is the ruling: no referee weighs it. Plain code places each change: a finding by its quote,
an instruction by a passage it quotes or a section title it names; an instruction naming neither is a rule
for the whole document and goes to every section, whose writer applies it only where the section breaks it.
A section gets one revision with all its changes: two revisions of one section at once conflict.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard
from agentswarm.core.domain import Artifact, ArtifactId, Event, SectionAssigned, Sized, TeamName
from agentswarm.evaluation.redteam import DocumentRedTeam, Finding
from agentswarm.teams.review import ReviewCampaign


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Placed(Frozen):
    """What one section's writer is asked to change."""

    section: str  # its file, e.g. sections/03-batching.md
    instructions: tuple[str, ...]  # the user's, in their words
    findings: tuple[Finding, ...]  # the red team's, kept by the user


class Placement(Frozen):
    sections: tuple[Placed, ...]
    unplaced: tuple[Finding, ...]  # findings no section holds (the references, say): shown, not fixed


class UserRevisions:
    NAME: ClassVar[TeamName] = TeamName("user")
    QUOTED: ClassVar[re.Pattern[str]] = re.compile(r'"([^"]+)"|“([^”]+)”')
    HEADING: ClassVar[re.Pattern[str]] = re.compile(r"^#{1,6}\s+(?:\d+(?:\.\d+)*\.?\s+)?(.+?)\s*$", re.M)
    MIN_TITLE = 6  # characters: a shorter heading ("Setup") is too likely to appear in passing

    def __init__(
        self, board: Blackboard, create: Callable[[str, TeamName], Artifact], brief: str = "docs/brief.md"
    ) -> None:
        self._board = board
        self._create = create
        self._brief = brief

    @classmethod
    def quotes(cls, instruction: str) -> tuple[str, ...]:
        """The passages an instruction quotes, long enough to find by search."""
        found = (a or b for a, b in cls.QUOTED.findall(instruction))
        return tuple(q for q in found if len(DocumentRedTeam.flat(q)) >= 12)

    @classmethod
    def title(cls, text: str) -> str | None:
        heading = cls.HEADING.search(text)
        return heading.group(1).strip("*_ ") if heading is not None else None

    @classmethod
    def missing(cls, instruction: str, document: str) -> str | None:
        """A passage the instruction quotes that the document doesn't hold word for word, or None."""
        return next((q for q in cls.quotes(instruction) if not DocumentRedTeam.quoted(q, document)), None)

    @classmethod
    def place(
        cls, instructions: Sequence[str], findings: Sequence[Finding], sections: Mapping[str, str]
    ) -> Placement:
        asked: dict[str, list[str]] = {path: [] for path in sections}
        for instruction in instructions:
            quoted = cls.quotes(instruction)
            at = [p for p, text in sections.items() if any(DocumentRedTeam.quoted(q, text) for q in quoted)]
            if not at:
                said = instruction.lower()
                at = [
                    p
                    for p, text in sections.items()
                    if (t := cls.title(text)) is not None and len(t) >= cls.MIN_TITLE and t.lower() in said
                ]
            for path in at or sections:  # naming no place: a rule for the whole document
                asked[path].append(instruction)
        fixed: dict[str, list[Finding]] = {path: [] for path in sections}
        unplaced: list[Finding] = []
        for finding in findings:
            at = next(
                (p for p, text in sections.items() if DocumentRedTeam.quoted(finding.evidence, text)), None
            )
            if at is None:
                unplaced.append(finding)
            else:
                fixed[at].append(finding)
        placed = tuple(
            Placed(section=p, instructions=tuple(asked[p]), findings=tuple(fixed[p]))
            for p in sections
            if asked[p] or fixed[p]
        )
        return Placement(sections=placed, unplaced=tuple(unplaced))

    def task(self, placed: Placed) -> str:
        listed = [f'(the user) "{i}"' for i in placed.instructions] + [
            f'(a red-team finding the user agreed with) {f.flaw} The passage: "{f.evidence}". '
            f"Suggested: {f.fix}"
            for f in placed.findings
        ]
        numbered = " ".join(f"({n}) {item}" for n, item in enumerate(listed, 1))
        return (
            f"Revise {placed.section} as the user asks after reading the whole document: {numbered} "
            "The user's word settles what they ask; where an instruction is a rule for the whole document, "
            "apply it where this section breaks it and leave the section as it is otherwise. Keep every "
            f"factual statement cited to its source, follow {self._brief} (its length included), and "
            "change only what these need."
        )

    def create(self, placement: Placement) -> tuple[ArtifactId, ...]:
        """One revision task a section, written by the section's writer as it wrote it."""
        views = self._board.current()
        events: list[Event] = []
        tasks: list[ArtifactId] = []
        for placed in placement.sections:
            task = self._create(self.task(placed), self.NAME).id
            tasks.append(task)
            assigned = ReviewCampaign.assignment(placed.section, views)
            if assigned is not None:
                events.append(SectionAssigned(artifact=task, path=placed.section, section=assigned.section))
            events.append(Sized(artifact=task, fits=True))
        self._board.append(events)
        return tuple(tasks)
