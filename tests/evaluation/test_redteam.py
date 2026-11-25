from pathlib import Path

from pydantic import BaseModel

from agentswarm.evaluation.citations import CitationAnswer, CitationAudit, CitationRequest, Support
from agentswarm.evaluation.redteam import (
    DocumentAttack,
    DocumentRedTeam,
    Finding,
    Findings,
    FlawKind,
    RedTeamReport,
    ReferenceCheck,
    SourceCheck,
)
from agentswarm.llm.workers import Toolbox
from tests.support.fakes import ScriptedWorker

DOCUMENT = """# Sleep

## What sleep does

Section 5 asked what a machine can decide, and this section asks how **memory** is kept.
Sleep helps memory[^1].

## References

[^1]: example.com (n.d.). [*Sleep - Example Site*](https://example.com/sleep).
"""


def finding(evidence: str) -> Finding:
    return Finding(where="What sleep does", evidence=evidence, flaw="f", fix="x", kind=FlawKind.REVISE)


class TestDocumentRedTeam:
    async def test_a_flaw_is_kept_only_when_its_quote_is_in_the_document(self) -> None:
        def attack(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            assert isinstance(payload, DocumentAttack) and payload.words == 3000
            return Findings(
                findings=[
                    finding("Section 5 asked what a machine can decide"),
                    finding("this section asks how memory is kept."),  # emphasis dropped: still found
                    finding("[^1]: example.com (n.d.)"),
                    finding("Section 4 explained the hippocampus"),  # invented
                ]
            )

        request = DocumentAttack(
            today="2026-11-23",
            reader="an adult",
            brief="b",
            words=3000,
            sources=("a.txt",),
            document=DOCUMENT,
        )
        report = await DocumentRedTeam(ScriptedWorker({Findings: attack})).attack(request)
        assert len(report.shown) == 3 and [f.evidence for f in report.unshown] == [
            "Section 4 explained the hippocampus"
        ]
        assert "3 flaws shown, 1 dropped" in report.render()

    def test_a_quote_may_skip_text_with_an_ellipsis_but_every_part_must_be_there(self) -> None:
        assert DocumentRedTeam.quoted("Section 5 asked what ... how memory is kept", DOCUMENT)
        assert not DocumentRedTeam.quoted("Section 5 asked what ... how dreams are kept", DOCUMENT)
        assert not DocumentRedTeam.quoted("Sleep", DOCUMENT)  # too short to show anything


class TestReferenceCheck:
    def test_a_site_as_author_no_date_and_a_pages_leftovers_are_flagged(self) -> None:
        references = (
            "[^1]: Zach, R. (2003). [*Hilbert's Program*](https://plato.stanford.edu/x). SEP.\n"
            "[^2]: lmsys.org (n.d.). [*Deploying DeepSeek*](https://lmsys.org/x). lmsys.org.\n"
            "[^3]: github.com (n.d.). [*GitHub - lm-sys/RouteLLM: A framework*](https://github.com/x).\n"
            "[^4]: The SGLang Team (2025). [*Deploying DeepSeek*](https://lmsys.org/x). lmsys.org.\n"
        )
        issues = ReferenceCheck.issues(references)
        assert issues == (
            "[^2] Deploying DeepSeek: its author is the site lmsys.org; no date",
            "[^3] GitHub - lm-sys/RouteLLM: A framework: its author is the site github.com; no date; "
            "its title has a page's leftovers or is cut short",
        )


class TestSourceCheck:
    @staticmethod
    def level(tmp_path: Path) -> Path:
        (tmp_path / "sections").mkdir()
        (tmp_path / "sources").mkdir()
        (tmp_path / "sections" / "01-agreement.md").write_text(
            "## Agreement\n\nGPT-4 with single-answer grading reached 85% agreement with humans in S2 "
            "[@zheng-p002, page 7].\nThe judge disagrees in 8% of cases [@zheng-p002, page 8].\n"
        )
        (tmp_path / "sources" / "zheng-p002.md").write_text("[page 7]\nG4-Single 85% in S2.\n[page 8]\nNo.\n")
        return tmp_path

    async def test_a_fact_finding_the_cited_source_contradicts_is_dropped_and_counted(
        self, tmp_path: Path
    ) -> None:
        def rule(payload: BaseModel, toolbox: Toolbox | None) -> BaseModel:
            assert isinstance(payload, CitationRequest)
            supported = "85%" in payload.statement
            return CitationAnswer(
                reason='"G4-Single 85% in S2"' if supported else "the page says no such thing",
                support=Support.SUPPORTED if supported else Support.UNSUPPORTED,
            )

        audit = CitationAudit(self.level(tmp_path), ScriptedWorker({CitationAnswer: rule}))
        wrong = finding("GPT-4 with single-answer grading reached 85% agreement with humans in S2[^5].")
        right = finding("The judge disagrees in 8% of cases[^5].")
        style = finding("this section asks how memory is kept")  # not a fact: shown without a check
        report = RedTeamReport(
            shown=(
                wrong.model_copy(update={"fact": True}),
                right.model_copy(update={"fact": True}),
                style,
            ),
            unshown=(),
        )
        checked = await SourceCheck(audit).check(report)
        assert [f.evidence for f in checked.shown] == [right.evidence, style.evidence]
        (refuted,) = checked.refuted
        assert refuted.finding.evidence == wrong.evidence and "85%" in refuted.reason
        assert "1 refuted by the cited source" in checked.render()

    def test_a_quote_finds_its_statement_without_the_markers_and_a_short_one_finds_none(
        self, tmp_path: Path
    ) -> None:
        audit = CitationAudit(self.level(tmp_path), ScriptedWorker({}))
        check = SourceCheck(audit)
        found = check.statement("single-answer grading reached 85% agreement with humans in S2[^5].")
        assert found is not None and found[0] == "01-agreement.md"
        assert check.statement("85% agreement") is None  # too short to tell statements apart
