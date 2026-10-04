"""Step 2: claims. One team at a time; an expired lease can be taken over.

    uv run tutorial/02_claims.py

Every value is built on its own line and named, so you can print or inspect any of them.
"""

from datetime import timedelta

from agentswarm.board.blackboard import Blackboard
from agentswarm.board.claims import ClaimRegistry
from agentswarm.board.eventlog import InMemoryEventLog
from agentswarm.core.clock import FakeClock
from agentswarm.core.domain import Artifact, ArtifactCreated, ArtifactId, Level, ProjectId, TeamName

# The same board as in step 1, with one artifact on it.
clock = FakeClock()
project = ProjectId("demo")
log = InMemoryEventLog(project, clock)
board = Blackboard(log)
artifact_id = ArtifactId("a-0001")
raw = Level("raw")
human = TeamName("human")
task = Artifact(
    id=artifact_id, task="Add farewell", branch="raw/a-0001-farewell", level=raw, created_by=human
)
created = ArtifactCreated(artifact=task)
board.append([created])

# Claims go through a registry over the board. A lease lasts ten minutes.
claims = ClaimRegistry(board, clock)
lease = timedelta(minutes=10)
executor = TeamName("executor")
red_team = TeamName("red_team")

# 1. One team at a time: while the executor holds the artifact, the red team is refused.
mine = claims.claim(artifact_id, executor, lease)
print("executor's claim:", mine)  # a Claim with an id and an expiry

refused = claims.claim(artifact_id, red_team, lease)
print("red team's claim while the executor holds it:", refused)  # None: taken

# 2. The normal case: the executor finishes and gives the claim back at once, long before it expires.
assert mine is not None
claims.release(mine)
theirs = claims.claim(artifact_id, red_team, lease)
print("red team's claim right after the release:", theirs)  # a new Claim, no waiting

# 3. The fallback: the red team crashes and never releases. Once its lease runs out, the executor can
#    take the artifact over again.
eleven_minutes = timedelta(minutes=11)
clock.advance(eleven_minutes)
again = claims.claim(artifact_id, executor, lease)
print("executor's claim after the red team's lease expired:", again)  # a new Claim

# 4. If the crashed red team comes back with a result, it is thrown away: someone else holds it now.
assert theirs is not None
views = board.current()
version = views.version(artifact_id)
published = claims.publish(theirs, version, [])
accepted = published is not None
print("red team's late result accepted:", accepted)  # False
