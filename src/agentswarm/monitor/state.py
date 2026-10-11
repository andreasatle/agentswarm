"""What the web monitor shows: a read model of a project's board (`agentswarm monitor`).

The board is an event log, so watching it needs nothing new recorded: this folds the log into four views,
from overview to detail ("overview first, zoom and filter, then details on demand"), which keeps a project
with many teams readable:

- `Overview`: the definition of done over time (seen and held-out tests), spend against the day's budget,
  open escalations, and how many steps are at each level;
- `TeamRow`s: per team, what it is working on now, and what it has done (commits, failures, merges, spend);
- `Node`s: the plan as a tree, each step with its state;
- `Detail`: one step's history, line by line.

It only reads, so it can't disturb a run; it is rebuilt from the log on every request.
"""

from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from agentswarm.board.blackboard import Blackboard, BoardViews
from agentswarm.board.eventlog import EventLog
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import (
    ArtifactId,
    AttemptFailed,
    Claim,
    Claimed,
    CommitPushed,
    Envelope,
    Promoted,
    Rejected,
    Released,
    Scored,
    Spent,
    TeamName,
)
from agentswarm.monitor.report import Report


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Status(StrEnum):
    """A step's state, as the tree colours it."""

    DONE = "done"  # at the top level
    MERGED = "merged"  # past the lowest level, not yet at the top
    WORKING = "working"  # a team holds a claim on it
    SPLIT = "split"  # an umbrella: its children do the work
    ESCALATED = "escalated"  # waiting for a person
    DROPPED = "dropped"
    WAITING = "waiting"  # an earlier sibling goes first
    QUEUED = "queued"


class Point(Frozen):
    at: datetime
    seen: float | None  # share of the tests the agents see
    held_out: float | None
    spent_usd: float


class Overview(Frozen):
    project: str
    north_star: str
    now: datetime
    head: int  # the log's last event: the page refreshes only when it moves
    spent_usd: float
    billed_usd: float  # the part the providers reported charging; the rest is estimated from prices
    spent_today_usd: float
    budget_per_day_usd: float | None
    progress: tuple[Point, ...]
    levels: dict[str, int]  # steps (umbrellas excluded) at each level
    escalations: tuple[str, ...]  # open ones: "<artifact>: <reason>", or project-wide
    paused: bool


class TeamRow(Frozen):
    team: TeamName
    working_on: ArtifactId | None
    task: str | None
    since: datetime | None
    overdue: bool = False  # still working past its claim's lease (a slow local model, or a stuck one)
    commits: int
    failures: int  # discarded attempts
    merged: int  # its commits that were promoted
    spent_usd: float


class ModelRow(Frozen):
    model: str  # the name in the project's `models`
    attempts: int  # commits and discarded attempts
    merged: int  # its commits that were promoted
    spent_usd: float


class Node(Frozen):
    id: ArtifactId
    parent: ArtifactId | None
    task: str
    level: str
    status: Status
    team: TeamName | None  # who works on it now
    last_model: str | None  # the model of its last commit or failed attempt
    attempts: int
    spent_usd: float


class Detail(Frozen):
    id: ArtifactId
    task: str
    level: str
    lines: tuple[tuple[datetime, str], ...]


class Summary(Frozen):
    """One project's row in the list of all projects."""

    project: str
    north_star: str
    started: bool  # it has a board yet
    seen: float | None = None
    held_out: float | None = None
    spent_today_usd: float = 0.0
    budget_per_day_usd: float | None = None
    working: int = 0  # steps a team is working on now
    escalations: int = 0
    paused: bool = False
    last_event: datetime | None = None


class State(Frozen):
    overview: Overview
    teams: tuple[TeamRow, ...]
    models: tuple[ModelRow, ...]
    nodes: tuple[Node, ...]


class MonitorState:
    def __init__(self, config: ProjectConfig, log: EventLog) -> None:
        self._config = config
        self._log = log

    def state(self, now: datetime | None = None) -> State:
        now = now or datetime.now(UTC)
        envelopes = self._log.read()
        views = Blackboard(self._log).rebuild()
        return State(
            overview=self.overview(views, envelopes, now),
            teams=self.teams(views, envelopes, now),
            models=self.models(envelopes),
            nodes=self.nodes(views, envelopes, now),
        )

    def summary(self, now: datetime | None = None) -> Summary:
        state = self.state(now)
        o = state.overview
        last = o.progress[-1] if o.progress else None
        envelopes = self._log.read()
        return Summary(
            project=o.project,
            north_star=o.north_star,
            started=True,
            seen=last.seen if last else None,
            held_out=last.held_out if last else None,
            spent_today_usd=o.spent_today_usd,
            budget_per_day_usd=o.budget_per_day_usd,
            working=sum(n.status is Status.WORKING for n in state.nodes),
            escalations=len(o.escalations),
            paused=o.paused,
            last_event=envelopes[-1].at if envelopes else None,
        )

    def detail(self, id: ArtifactId) -> Detail | None:
        views = Blackboard(self._log).rebuild()
        if id not in {a.id for a in views.artifacts()}:
            return None
        artifact = views.artifact(id)
        lines = tuple(
            (e.at, Report.line(e.event))
            for e in self._log.read()
            if e.event.subject == id and Report.shown(e.event)
        )
        return Detail(id=id, task=artifact.task, level=artifact.level, lines=lines)

    def overview(self, views: BoardViews, envelopes: list[Envelope], now: datetime) -> Overview:
        usd, _ = views.spent()
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        progress = tuple(
            Point(
                at=e.at,
                seen=e.event.seen.share,
                held_out=e.event.held_out.share if e.event.held_out is not None else None,
                spent_usd=e.event.spent_usd,
            )
            for e in envelopes
            if isinstance(e.event, Scored)
        )
        levels = Counter(a.level for a in views.artifacts() if not views.is_umbrella(a.id))
        escalations = [f"{e.artifact}: {e.reason}" for e in views.open_escalations() if e.artifact]
        escalations += [f"project: {e.reason}" for e in views.project_escalations()[-3:]]
        return Overview(
            project=self._config.project,
            north_star=self._config.north_star,
            now=now,
            head=envelopes[-1].seq if envelopes else 0,
            spent_usd=round(usd, 4),
            billed_usd=round(views.billed(), 4),
            spent_today_usd=round(views.spent_since(day), 4),
            budget_per_day_usd=self._config.budgets.usd_per_day,
            progress=progress,
            levels={level: levels.get(level, 0) for level in self._config.levels.levels},
            escalations=tuple(escalations),
            paused=views.paused,
        )

    @staticmethod
    def open_claims(envelopes: list[Envelope]) -> dict[TeamName, tuple[Claim, datetime]]:
        """Claims not yet released, with when they were taken: a team still works past its lease (the board's
        views drop a claim when its lease expires, so another team may take the artifact over)."""
        held: dict[str, tuple[Claim, datetime]] = {}
        for e in envelopes:
            match e.event:
                case Claimed(claim=claim) if claim.id is not None:
                    held[claim.id] = (claim, e.at)
                case Released(claim=str() as claim):
                    held.pop(claim, None)
                case _:
                    pass
        return {claim.team: (claim, at) for claim, at in held.values()}

    def teams(self, views: BoardViews, envelopes: list[Envelope], now: datetime) -> tuple[TeamRow, ...]:
        working = self.open_claims(envelopes)
        commits: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        merged: Counter[str] = Counter()
        spent: dict[str, float] = {}
        last_commit_by: dict[ArtifactId, str] = {}
        for e in envelopes:
            match e.event:
                case CommitPushed(model=str()):
                    team = self.committer(views, e.event.artifact)
                    commits[team] += 1
                    last_commit_by[e.event.artifact] = team
                case AttemptFailed():
                    failures[e.event.team] += 1
                case Promoted() if e.event.artifact in last_commit_by:
                    merged[last_commit_by.pop(e.event.artifact)] += 1
                case Spent():
                    spent[e.event.by] = spent.get(e.event.by, 0.0) + e.event.usd
                case _:
                    pass
        names = {*working, *commits, *failures, *(t.use for t in self._config.teams)}
        names |= {name for name in spent if not name.startswith("jev")}
        rows: list[TeamRow] = []
        for name in sorted(names):
            claim, since = working.get(TeamName(name), (None, None))
            rows.append(
                TeamRow(
                    team=TeamName(name),
                    working_on=claim.artifact if claim else None,
                    task=views.artifact(claim.artifact).task if claim else None,
                    since=since,
                    overdue=claim is not None and claim.expires_at < now,
                    commits=commits[name],
                    failures=failures[name],
                    merged=merged[name],
                    spent_usd=round(spent.get(name, 0.0), 4),
                )
            )
        return tuple(rows)

    @staticmethod
    def models(envelopes: list[Envelope]) -> tuple[ModelRow, ...]:
        """Per model: attempts, merges and spend: which model did what, at what cost."""
        attempts: Counter[str] = Counter()
        merged: Counter[str] = Counter()
        spent: dict[str, float] = {}
        last: dict[ArtifactId, str] = {}
        for e in envelopes:
            match e.event:
                case CommitPushed(model=str() as model):
                    attempts[model] += 1
                    last[e.event.artifact] = model
                case AttemptFailed(model=str() as model):
                    attempts[model] += 1
                case Promoted() if e.event.artifact in last:
                    merged[last.pop(e.event.artifact)] += 1
                case Spent() if not e.event.model.startswith("jev"):
                    spent[e.event.model] = spent.get(e.event.model, 0.0) + e.event.usd
                case _:
                    pass
        names = sorted({*attempts, *spent}, key=lambda m: -spent.get(m, 0.0))
        return tuple(
            ModelRow(model=m, attempts=attempts[m], merged=merged[m], spent_usd=round(spent.get(m, 0.0), 4))
            for m in names
        )

    @staticmethod
    def committer(views: BoardViews, artifact: ArtifactId) -> str:
        """The team behind an artifact's model commits: the executor family (commits carry no team)."""
        failed = [e.team for e in views.history(artifact) if isinstance(e, AttemptFailed)]
        return failed[-1] if failed else "executor"

    def nodes(self, views: BoardViews, envelopes: list[Envelope], now: datetime) -> tuple[Node, ...]:
        top = self._config.levels.levels[-1]
        lowest = self._config.levels.lowest
        spent: dict[str, float] = {}
        for e in envelopes:
            if isinstance(e.event, Spent) and e.event.artifact is not None:
                spent[e.event.artifact] = spent.get(e.event.artifact, 0.0) + e.event.usd
        held = {claim.artifact: claim for claim, _ in self.open_claims(envelopes).values()}
        nodes: list[Node] = []
        for artifact in views.artifacts():
            history = views.history(artifact.id)
            claim = held.get(artifact.id)
            models = [e.model for e in history if isinstance(e, CommitPushed | AttemptFailed) and e.model]
            attempts = sum(isinstance(e, CommitPushed | AttemptFailed) for e in history)
            nodes.append(
                Node(
                    id=artifact.id,
                    parent=artifact.parent,
                    task=artifact.task,
                    level=artifact.level,
                    status=self.status(views, artifact.id, artifact.level, top, lowest, claim is not None),
                    team=claim.team if claim else None,
                    last_model=models[-1] if models else None,
                    attempts=attempts,
                    spent_usd=round(spent.get(artifact.id, 0.0), 4),
                )
            )
        return tuple(nodes)

    @staticmethod
    def status(views: BoardViews, id: ArtifactId, level: str, top: str, lowest: str, claimed: bool) -> Status:
        if views.dropped(id):
            return Status.DROPPED
        if level == top:
            return Status.DONE
        if views.open_escalation(id) is not None:
            return Status.ESCALATED
        if views.is_umbrella(id):
            return Status.SPLIT
        if claimed:
            return Status.WORKING
        if level != lowest:
            return Status.MERGED
        if views.waits_for(id) is not None:
            return Status.WAITING
        last = [e for e in views.history(id) if isinstance(e, Rejected | Promoted)]
        return Status.QUEUED if not last or isinstance(last[-1], Rejected) else Status.MERGED
