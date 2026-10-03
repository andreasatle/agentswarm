"""Sample values shared by the tests."""

from datetime import UTC, datetime

from agentswarm.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    CommitPushed,
    Finding,
    FindingKind,
    Level,
    ProjectId,
    Severity,
    TeamName,
)


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
    def finding(cls, id: ArtifactId = A, level: Level = RAW) -> Finding:
        return Finding(
            artifact=id,
            level=level,
            team=cls.EXECUTOR,
            kind=FindingKind.INCOMPLETE,
            severity=Severity.MAJOR,
            detail="Does not address every part of the task",
        )
