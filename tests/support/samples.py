"""Sample values shared by the tests."""

from datetime import UTC, datetime

from agentswarm.core.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    CommitPushed,
    Finding,
    FindingId,
    FindingKind,
    Level,
    ProjectId,
    Severity,
    TeamName,
)
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.judges import ChangeReadiness


class Samples:
    PROJECT = ProjectId("demo")
    A = ArtifactId("a-0001")
    B = ArtifactId("a-0002")
    RAW = Level("raw")
    EXPERIMENTAL = Level("experimental")
    EXECUTOR = TeamName("executor")
    TESTER = TeamName("tester")
    AT = datetime(2026, 10, 3, tzinfo=UTC)

    @classmethod
    def artifact(cls, id: ArtifactId = A, task: str = "add retry") -> Artifact:
        return Artifact(id=id, task=task, branch=f"raw/{id}-work", level=cls.RAW, created_by=cls.EXECUTOR)

    @classmethod
    def created(cls, id: ArtifactId = A) -> ArtifactCreated:
        return ArtifactCreated(artifact=cls.artifact(id))

    @classmethod
    def commit(cls, id: ArtifactId = A, sha: str = "c0ffee") -> CommitPushed:
        return CommitPushed(artifact=id, commit=sha)

    @classmethod
    def finding(cls, id: ArtifactId = A, level: Level = RAW, finding: str = "f-sample") -> Finding:
        return Finding(
            id=FindingId(finding),
            artifact=id,
            level=level,
            team=cls.EXECUTOR,
            kind=FindingKind.INCOMPLETE,
            severity=Severity.MAJOR,
            detail="Does not address every part of the task",
        )


class Judged:
    """Answers to the ChangeReadiness judge, as p(yes) per question."""

    @staticmethod
    def answers(
        in_scope: float = 0.95, tests_relevant: float = 0.95, complete: float = 0.95
    ) -> ChangeReadiness:
        return ChangeReadiness(
            in_scope=NoulAnswer(noul=in_scope),
            tests_relevant=NoulAnswer(noul=tests_relevant),
            complete=NoulAnswer(noul=complete),
        )

    @classmethod
    def passing(cls) -> ChangeReadiness:
        return cls.answers()

    @classmethod
    def incomplete(cls) -> ChangeReadiness:
        return cls.answers(complete=0.05)

    @classmethod
    def unsure(cls) -> ChangeReadiness:
        return cls.answers(tests_relevant=0.6)
