"""Step 2: claims. One team at a time; an expired lease can be taken over.

uv run tutorial/02_claims.py
"""

from datetime import timedelta

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import Artifact, ArtifactCreated, ArtifactId, Level, ProjectId, TeamName

clock = FakeClock()
board = Blackboard(InMemoryEventLog(ProjectId("demo"), clock))
task = Artifact(
    id=ArtifactId("a-0001"),
    task="Add farewell",
    branch="raw/a-0001-farewell",
    level=Level("raw"),
    created_by=TeamName("human"),
)
board.append([ArtifactCreated(artifact=task)])

claims = ClaimRegistry(board, clock)
mine = claims.claim(task.id, TeamName("executor"), timedelta(minutes=10))
print("executor claimed it:", mine is not None)  # True
print("red team's claim:", claims.claim(task.id, TeamName("red_team"), timedelta(minutes=10)))  # None: taken

clock.advance(timedelta(minutes=11))  # the executor crashed and its lease ran out...
theirs = claims.claim(task.id, TeamName("red_team"), timedelta(minutes=10))
print("red team claimed it after the lease expired:", theirs is not None)  # True

# The executor's late result is discarded: someone else holds the artifact now.
assert mine is not None
print(
    "executor's late result accepted:", claims.publish(mine, board.current().version(task.id), []) is not None
)
