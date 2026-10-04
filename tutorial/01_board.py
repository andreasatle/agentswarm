"""Step 1: the board. Events in, views out; replaying the log gives the same views.

    uv run tutorial/01_board.py

Every value is built on its own line and named, so you can print or inspect any of them.
"""

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import (
    Artifact,
    ArtifactCreated,
    ArtifactId,
    CommitPushed,
    Level,
    ProjectId,
    TeamName,
)

# The pieces of a board: a clock, a project id, a log of events, and the board over that log.
clock = FakeClock()
project = ProjectId("demo")
log = InMemoryEventLog(project, clock)
board = Blackboard(log)

# An artifact: one task on its own branch at the lowest level.
artifact_id = ArtifactId("a-0001")
raw = Level("raw")
human = TeamName("human")
task = Artifact(
    id=artifact_id, task="Add farewell", branch="raw/a-0001-farewell", level=raw, created_by=human
)

# Appending an event is the only way to change anything; it returns the new views.
created = ArtifactCreated(artifact=task)
views = board.append([created])
at_raw = views.artifacts_at(raw)
ids_at_raw = [artifact.id for artifact in at_raw]
print("at raw:", ids_at_raw)  # ['a-0001']

pushed = CommitPushed(artifact=artifact_id, commit="c0ffee")
views = board.append([pushed])
unjudged = views.unjudged_commit(artifact_id)
version = views.version(artifact_id)
print("unjudged commit:", unjudged)  # c0ffee
print("version:", version)  # 2: the artifact changed twice

# Keep the views the board has built up, event by event, then fold the whole log again into new views.
# Two separate objects with the same content: the log alone is enough to recreate the state.
before = board.current()
replayed = board.rebuild()
separate = replayed is not before
same = replayed == before
print("rebuilt views are a new object:", separate)  # True
print("replay gives the same views:", same)  # True
