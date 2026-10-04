"""Step 1: the board. Events in, views out; replaying the log gives the same views.

uv run tutorial/01_board.py
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

clock = FakeClock()
board = Blackboard(InMemoryEventLog(ProjectId("demo"), clock))
task = Artifact(
    id=ArtifactId("a-0001"),
    task="Add farewell",
    branch="raw/a-0001-farewell",
    level=Level("raw"),
    created_by=TeamName("human"),
)
views = board.append([ArtifactCreated(artifact=task)])
print("at raw:", [a.id for a in views.artifacts_at(Level("raw"))])  # ['a-0001']

views = board.append([CommitPushed(artifact=task.id, commit="c0ffee")])
print("unjudged commit:", views.unjudged_commit(task.id))  # c0ffee
print("version:", views.version(task.id))  # 2: the artifact changed twice
print("replay gives the same views:", board.rebuild() == board.current())  # True
