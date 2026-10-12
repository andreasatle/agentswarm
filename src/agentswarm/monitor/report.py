"""Text reports of a board: one line per event, and every artifact's history (`agentswarm status`)."""

from collections import Counter

from agentswarm.board.blackboard import BoardViews
from agentswarm.core.domain import (
    AcceptanceRestored,
    AcceptanceWritten,
    ArtifactCreated,
    AttemptFailed,
    CheckDisputed,
    Claimed,
    CommitPushed,
    Escalated,
    EscalationResolved,
    Event,
    FindingAdded,
    FindingResolved,
    OutcomeRecorded,
    Paused,
    Profiled,
    Promoted,
    PromotionSettled,
    PromotionStarted,
    Rejected,
    Released,
    Repaired,
    Resumed,
    Routed,
    Scored,
    SignedOff,
    Sized,
    Spent,
    ToolsUsed,
    Unbuildable,
)
from agentswarm.evaluation.score import Progress


class Report:
    """Renders an artifact's history as one line per event."""

    @staticmethod
    def line(event: Event) -> str:
        match event:
            case ArtifactCreated():
                return f"created at {event.artifact.level}: {event.artifact.task}"
            case Claimed():
                return f"claimed by {event.claim.team}"
            case Released():
                return f"released by {event.team}"
            case CommitPushed():
                by = f" by {event.model}" if event.model else ""
                return f"commit {event.commit[:10]}{by}"
            case FindingAdded():
                return f"finding {event.finding.id} ({event.finding.kind}): {event.finding.detail}"
            case FindingResolved():
                by = f" by {event.resolved_by}" if event.resolved_by else ""
                return f"finding {event.finding} resolved{by}: {event.reason}"
            case SignedOff():
                return f"signed off by {event.team}"
            case Spent():
                cost = f"${event.usd:.4f}" if event.priced else "unpriced"
                tokens = f"{event.input_tokens}+{event.output_tokens} tokens"
                return f"spent by {event.by} on {event.model}: {tokens}, {cost}"
            case Repaired():
                result = "checks pass" if event.passed else "checks still fail"
                return f"repair by {event.model or event.team}: {result}"
            case Unbuildable():
                return f"every model of {event.team} failed, back to the planner: {event.reason}"
            case Scored():
                held = f", held out {Progress.tally(event.held_out)}" if event.held_out is not None else ""
                return f"scored {event.level} at {event.commit[:10]}: seen {Progress.tally(event.seen)}{held}"
            case Paused():
                return f"PAUSED: {event.reason}"
            case Resumed():
                return f"resumed: {event.reason}"
            case Sized():
                return "fits one branch" if event.fits else f"split into {', '.join(event.children)}"
            case Promoted():
                return f"promoted {event.from_level} -> {event.to_level}"
            case Routed():
                score = event.difficulty
                difficulty = f"difficulty {score:.2f}" if score is not None else "no score"
                how = "chosen from the ledger" if event.acted else "cheapest tier"
                return f"{event.call_kind} routed to {event.model} ({difficulty}, {how})"
            case Profiled():
                return f"profiled: difficulty {event.difficulty:.2f}{'' if event.active else ' (shadow)'}"
            case AttemptFailed():
                return f"attempt by {event.model or event.team} discarded: {event.reason}"
            case ToolsUsed():
                tools = Counter(c.tool for c in event.calls)
                read = sum(c.output_chars for c in event.calls)
                listed = ", ".join(f"{t} x{n}" for t, n in tools.most_common())
                return f"tool calls by {event.model or event.team}: {listed}; {read:,} characters returned"
            case OutcomeRecorded():
                verdict = "success" if event.success else "failure"
                return f"outcome for routing: {event.call_kind} by {event.model_id}, {verdict}"
            case AcceptanceWritten():
                return f"acceptance tests by {event.team}: {', '.join(f.path for f in event.files)}"
            case AcceptanceRestored():
                return f"acceptance tests restored after the producer changed them: {', '.join(event.paths)}"
            case CheckDisputed():
                verdict = "upheld, test skipped" if event.upheld else "rejected, test stands"
                return f"{event.model or 'builder'} disputed {event.test}: {verdict} ({event.ruling})"
            case PromotionStarted():
                return f"moving {event.branch} to {event.target[:10]}"
            case PromotionSettled():
                return f"{event.branch} {'moved' if event.moved else 'did not move'}"
            case Rejected():
                return "rejected: " + "; ".join(f"{f.kind}: {f.detail}" for f in event.findings)
            case Escalated():
                label = "ESCALATED" if event.parks else "ESCALATED (notice; others carry on)"
                return f"{label}: {event.reason}"
            case EscalationResolved():
                note = f": {event.note}" if event.note else ""
                return f"escalation resolved ({event.resolution}){note}"

    @staticmethod
    def shown(event: Event) -> bool:
        """Whether an artifact's history shows it: claims and releases are bookkeeping."""
        return not isinstance(event, Claimed | Released)

    @classmethod
    def render(cls, views: BoardViews) -> str:
        usd, unpriced = views.spent()
        billed = views.billed()
        split = f"${billed:.2f} billed by providers, ${usd - billed:.2f} estimated from prices"
        header = [f"spent: ${usd:.2f} ({split})" + (f"; {unpriced} unpriced model runs" if unpriced else "")]
        header += [f"ESCALATED (project): {e.reason}" for e in views.project_escalations()[-5:]]
        header += ["PAUSED"] if views.paused else []
        header += [cls.line(views.scores()[-1])] if views.scores() else []
        blocks: list[str] = ["\n".join(header)]
        for artifact in views.artifacts():
            lines = [f"{artifact.id} [{artifact.level}] {artifact.task}"]
            lines += [f"  - {cls.line(e)}" for e in views.history(artifact.id) if cls.shown(e)]
            blocks.append("\n".join(lines))
        if len(blocks) == 1:
            blocks.append("(no artifacts)")
        return "\n\n".join(blocks)
