from collections.abc import Sequence

from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import (
    ArtifactId,
    Attacked,
    AttackFinding,
    CallKind,
    Checked,
    Event,
    OutcomeRecorded,
    OutlineEntry,
    Perspective,
    PromptId,
    RejectedReport,
    Routed,
    SectionAssigned,
    Spent,
    Support,
    TeamName,
)
from agentswarm.monitor.record import CheckKind, Maker, Row, RunRecord
from tests.support.samples import Samples

SECTION = "sections/01-intro.md"
WRITE = PromptId(name="SectionText", version="aaaaaaaaaaaa")
CHECK = PromptId(name="ClaimVerdict", version="bbbbbbbbbbbb")
RULE = PromptId(name="CitationAnswer", version="cccccccccccc")
HAIKU = "anthropic:claude-haiku-5-5"
SOL = "openai:gpt-6.1-sol"


def spent(
    by: str, model: str, prompt: PromptId | None, artifact: str | None = None, model_id: str | None = None
) -> Spent:
    return Spent(
        by=by,
        model=model,
        input_tokens=10,
        output_tokens=1,
        usd=0.0,
        priced=False,
        prompt=prompt,
        artifact=ArtifactId(artifact) if artifact else None,
        model_id=model_id,
    )


def record(events: Sequence[Event], model_ids: dict[str, str] | None = None) -> list[Row]:
    log = InMemoryEventLog(Samples.PROJECT, FakeClock(Samples.AT))
    log.append(events)
    return list(RunRecord(Samples.PROJECT, log.read(), model_ids).rows())


def assigned(artifact: str) -> SectionAssigned:
    entry = OutlineEntry(title="Intro", covers="c", sources=("p1",), words=100)
    return SectionAssigned(artifact=ArtifactId(artifact), path=SECTION, section=entry)


def checked(statement: str, support: Support | None) -> Checked:
    return Checked(
        by="claim-checker", file=SECTION, statement=statement, commit="c1", support=support, reason="r"
    )


class TestRunRecord:
    def test_a_checked_statement_is_tied_to_the_calls_that_wrote_and_judged_it(self) -> None:
        rows = record(
            [
                assigned("a-0001"),
                spent("jev:route:profile", "jev", None, "a-0001"),  # deciding the route isn't writing
                spent("writer", "cheap", WRITE, "a-0001", model_id=HAIKU),
                spent("claim-checker", "cheap", CHECK, model_id=HAIKU),
                checked("A [@p1].", Support.PARTLY),
                checked("B [@p1].", None),
            ]
        )
        assert [(r.kind, r.check, r.verdict, r.subject) for r in rows] == [
            (CheckKind.CLAIM, "claim-checker", "partly", "A [@p1]."),
            (CheckKind.CLAIM, "claim-checker", "unruled", "B [@p1]."),
        ]
        assert rows[0].producers == (Maker(by="writer", model=HAIKU, prompt=WRITE),)
        assert rows[0].checkers == (Maker(by="claim-checker", model=HAIKU, prompt=CHECK),)
        assert (rows[0].file, rows[0].commit) == (SECTION, "c1")

    def test_a_revision_counts_among_the_sections_makers_only_after_it_was_made(self) -> None:
        attack = Attacked(
            round=1,
            section=SECTION,
            focus=Perspective.CLAIMS,
            commit="c1",
            reported=2,
            confirmed=1,
            tasks=(ArtifactId("a-0002"),),
            findings=(AttackFinding(task=ArtifactId("a-0002"), claim="overstates", quotes=("A [@p1].",)),),
            rejected=(RejectedReport(claim="B", reason="it does say so", statement="B [@p1]."),),
        )
        rows = record(
            [
                assigned("a-0001"),
                spent("writer", "cheap", WRITE, "a-0001", model_id=HAIKU),
                spent("referee", "strong", RULE, model_id=SOL),
                attack,
                spent("writer", "strong", WRITE, "a-0002", model_id=SOL),  # the revision
                checked("A, revised [@p1].", Support.SUPPORTED),
            ]
        )
        rulings = [r for r in rows if r.kind is CheckKind.RULING]
        assert [(r.verdict, r.subject, r.reason) for r in rulings] == [
            ("confirmed", "A [@p1].", "overstates"),
            ("rejected", "B [@p1].", "it does say so"),
        ]
        assert rulings[0].checkers == (Maker(by="referee", model=SOL, prompt=RULE),)
        assert rulings[0].producers == (Maker(by="writer", model=HAIKU, prompt=WRITE),)
        assert [m.model for m in rows[-1].producers] == [HAIKU, SOL]

    def test_an_outcome_is_tied_to_the_teams_spend_with_that_model(self) -> None:
        rows = record(
            [
                spent("writer", "cheap", WRITE, "a-0001", model_id=HAIKU),
                spent("writer", "strong", WRITE, "a-0001", model_id=SOL),
                OutcomeRecorded(
                    artifact=ArtifactId("a-0001"),
                    team=TeamName("writer"),
                    call_kind=CallKind.PRODUCE,
                    model_id=SOL,
                    success=False,
                    settles=2,
                ),
            ]
        )
        (row,) = rows
        assert (row.kind, row.check, row.verdict, row.subject) == (
            CheckKind.OUTCOME,
            "outcome:produce",
            "failure",
            "a-0001",
        )
        assert row.producers == (Maker(by="writer", model=SOL, prompt=WRITE),)

    def test_older_boards_name_their_models_by_routing_or_by_the_config(self) -> None:
        routed = Routed(
            artifact=ArtifactId("a-0001"),
            team=TeamName("writer"),
            model="cheap",
            model_id=HAIKU,
            difficulty=None,
            decision=None,
            acted=False,
        )
        rows = record(
            [
                assigned("a-0001"),
                routed,
                spent("writer", "cheap", None, "a-0001"),
                spent("claim-checker", "checker", None),
                checked("A [@p1].", Support.SUPPORTED),
            ],
            model_ids={"checker": "ollama:gemma4:26b", "cheap": "stale:renamed"},
        )
        (row,) = rows
        assert row.producers == (Maker(by="writer", model=HAIKU, prompt=None),)  # routing wins
        assert row.checkers == (Maker(by="claim-checker", model="ollama:gemma4:26b", prompt=None),)
