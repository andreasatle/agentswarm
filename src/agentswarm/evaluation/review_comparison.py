"""Which review finds a document's unsupported citations, and at what cost? (the user, 2026-11-09: cheaper
runs; the reviewers are about 60% of a document run).

Three arms look at the same finished document: the facts reviewer as the review campaign runs it (one call
per section with its cited sources), and the claim checker (one cheap call per cited statement) on each
claim's cited part whole or on the passages hybrid search finds. The reference is the citation audit of every
cited statement, by a model of another family than both arms. An arm's statement counts as flagged when the
arm reports it not fully supported; a reviewer's finding is matched to the statements its quotes come from.
Nothing is written to the document: the arms' raw reports are compared, before any referee.
"""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentswarm.core.domain import Perspective
from agentswarm.evaluation.citations import Audited, CitationAudit, Support
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.teams.analyzer import Quotes
from agentswarm.teams.claims import CitedStatement, ClaimChecker
from agentswarm.teams.review import ReviewCampaign, ReviewFindings, ReviewRequest, ReviewSource


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Arm(StrEnum):
    FACTS_REVIEWER = "facts reviewer"
    CLAIMS_WHOLE = "claims, whole part"
    CLAIMS_RETRIEVED = "claims, retrieved"


class ArmResult(Frozen):
    arm: Arm
    flagged: tuple[str, ...]  # the statements (as in their files) reported not fully supported
    why: tuple[str, ...] = ()  # for each flagged statement, the arm's reason
    unmatched: int = 0  # findings about no cited statement (a reviewer may flag an uncited one)
    failed: int = 0  # calls that gave no answer
    usd: float = 0.0


class ComparisonRecord(Frozen):
    """Everything a comparison found, kept so a disagreement can be read without paying again."""

    reference: tuple[Audited, ...]
    arms: tuple[ArmResult, ...]


class ReviewComparison:
    def __init__(self, level: Path, north_star: str) -> None:
        self._level = level  # a level's plain folder: sections/, sources/, docs/brief.md
        self._north_star = north_star

    def statements(self) -> list[CitedStatement]:
        return [
            s
            for path in sorted((self._level / "sections").glob("*.md"))
            for s in CitedStatement.all_in(f"sections/{path.name}", path.read_text())
        ]

    def sources(self) -> dict[str, str]:
        return {p.stem: p.read_text() for p in sorted((self._level / "sources").glob("*.md"))}

    async def reference(self, worker: Worker, statements: Sequence[CitedStatement]) -> list[Audited]:
        audit = CitationAudit(self._level, worker)
        return await audit.audit([(s.file, s.text) for s in statements])

    async def claims(
        self, arm: Arm, checker: ClaimChecker, statements: Sequence[CitedStatement]
    ) -> ArmResult:
        checked = await checker.check(statements, self.sources())
        flagged = [c for c in checked if c.support in (Support.PARTLY, Support.UNSUPPORTED)]
        return ArmResult(
            arm=arm,
            flagged=tuple(c.statement.text for c in flagged),
            why=tuple(f"{c.support}: {c.reason}" for c in flagged),
            failed=sum(c.support is None for c in checked),
        )

    async def facts_reviewer(self, worker: Worker, statements: Sequence[CitedStatement]) -> ArmResult:
        """The facts perspective as the review campaign asks it, once per section; every finding is kept (the
        campaign takes the first three a round)."""
        brief_path = self._level / "docs" / "brief.md"
        brief = brief_path.read_text() if brief_path.is_file() else ""
        sources = self.sources()
        flagged: dict[str, str] = {}  # statement: the finding that flagged it
        unmatched = failed = 0
        for path in sorted((self._level / "sections").glob("*.md")):
            if not any(s.file == f"sections/{path.name}" for s in statements):
                continue  # a section none of the compared statements is in (`--limit`)
            text = path.read_text()
            request = ReviewRequest(
                north_star=self._north_star,
                brief=brief,
                section=f"sections/{path.name}",
                text=text,
                perspective=ReviewCampaign.PERSPECTIVE[Perspective.FACTS],
                sources=tuple(f"sources/{name}.md" for name in sources),
                material=self.material(text, sources),
            )
            try:
                report = await worker.run(ReviewCampaign.INSTRUCTIONS, request, ReviewFindings)
            except WorkerError:
                failed += 1
                continue
            mine = [s for s in statements if s.file == request.section]
            for finding in report.findings:
                quotes = [e.quote for e in finding.evidence if e.file.startswith("sections/")]
                hits = [s.text for s in mine if any(self.about(q, s) for q in quotes)]
                for hit in hits:
                    flagged.setdefault(hit, f"{finding.title}: {finding.problem}")
                unmatched += not hits
        return ArmResult(
            arm=Arm.FACTS_REVIEWER,
            flagged=tuple(flagged),
            why=tuple(flagged.values()),
            unmatched=unmatched,
            failed=failed,
        )

    @staticmethod
    def material(text: str, sources: Mapping[str, str]) -> tuple[ReviewSource, ...]:
        """The parts the section cites, within the campaign's limit (ReviewCampaign.material)."""
        found: list[ReviewSource] = []
        size = 0
        for name in dict.fromkeys(re.findall(r"\[@([^\],\s]+)", text)):
            if name not in sources:
                continue
            if size + len(sources[name]) > ReviewCampaign.MATERIAL_LIMIT:
                break
            found.append(ReviewSource(name=name, text=sources[name]))
            size += len(sources[name])
        return tuple(found)

    @staticmethod
    def about(quote: str, statement: CitedStatement) -> bool:
        """Whether a quote from the section is (part of) the statement, or holds it whole."""
        wanted, line = Quotes.normal(quote), Quotes.normal(statement.text)
        return len(wanted) >= Quotes.MIN_LENGTH and (
            wanted in line or Quotes.normal(statement.claim()) in wanted
        )

    @staticmethod
    def render(reference: Sequence[Audited], results: Sequence[ArmResult]) -> str:
        problems = {r.statement for r in reference if r.support in (Support.PARTLY, Support.UNSUPPORTED)}
        fine = {r.statement for r in reference if r.support is Support.SUPPORTED}
        unruled = sum(r.support is None for r in reference)
        lines = [
            f"reference: {len(reference)} cited statements, {len(problems)} not fully supported "
            f"({sum(r.support is Support.UNSUPPORTED for r in reference)} unsupported), {len(fine)} supported"
            + (f", {unruled} not ruled" if unruled else ""),
            "",
            "| arm | caught | missed | false alarms | other findings | failed calls | cost |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in results:
            flagged = set(r.flagged)
            lines.append(
                f"| {r.arm} | {len(flagged & problems)} of {len(problems)} | {len(problems - flagged)} | "
                f"{len(flagged & fine)} | {r.unmatched} | {r.failed} | ${r.usd:.3f} |"
            )
        return "\n".join(lines)
