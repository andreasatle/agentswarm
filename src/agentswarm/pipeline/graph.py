# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false
"""The document run as a LangGraph graph (the user's choice, 2026-11-01: a learning goal, and the tool made
for a fixed workflow that pauses for a person). The graph only orders the stages and pauses; every stage is a
plain class in stages.py. Its checkpointer keeps each run in SQLite between the user's commands, so a run
waits for an approval for as long as it takes and survives a restart.

    scope -> approve questions -> find -> approve sources -> write -> (decide on failed sections) -> audit ->
    (review -> audit) -> checked (the red team's look) -> approve publishing -> (revise -> audit -> checked
    -> approve publishing ...) -> publish

Before a paid stage, the run's spend so far and the stage's expected cost are checked against the budget; a
stage that wouldn't fit pauses the run for the user to raise the budget or stop.
"""

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar, Literal

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agentswarm.evaluation.documents import DocumentImport
from agentswarm.evaluation.redteam import Finding, FlawKind, ReferenceCheck
from agentswarm.llm.workers import WorkerError
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import (
    Answer,
    Choice,
    DocumentRun,
    Question,
    Revision,
    RunRequest,
    RunView,
    Stage,
    Working,
)
from agentswarm.pipeline.stages import Stages
from agentswarm.teams.revisions import UserRevisions

type Update = dict[str, Any]


class DocumentPipeline:
    THREADS = "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
    SOURCES_QUESTION = "Approve the sources"
    SCOPE_QUESTION = "Approve the questions"
    PUBLISHING_QUESTION = "Approve publishing"
    # Our types in a saved run, named for the checkpointer's serializer (it warns about, and will refuse,
    # types it wasn't told about)
    SAVED: ClassVar[list[tuple[str, str]]] = [
        ("agentswarm.evaluation.papers", "PaperSource"),
        ("agentswarm.evaluation.redteam", "Finding"),
        ("agentswarm.evaluation.redteam", "FlawKind"),
        *(
            (
                "agentswarm.pipeline.run",
                name,
            )
            for name in (
                "RunRequest",
                "Stage",
                "Choice",
                "Answer",
                "AuditResult",
                "DocumentRun",
                "Question",
                "Revision",
            )
        ),
    ]

    def __init__(self, stages: Stages, policy: Policy, checkpoints: sqlite3.Connection) -> None:
        self._stages = stages
        self._policy = policy
        self._db = checkpoints
        self._saver = SqliteSaver(checkpoints, serde=JsonPlusSerializer(allowed_msgpack_modules=self.SAVED))
        self._graph = self.build().compile(checkpointer=self._saver)

    def policy(self, run: DocumentRun) -> Policy:
        """The run's own policy file when it was started with one, else the default: `approve` reloaded the
        default, so a comparison's policy was dropped after the first pause (moe-systems-3, 2026-11-22)."""
        return Policy.load(run.request.policy) if run.request.policy is not None else self._policy

    @classmethod
    def open(cls, stages: Stages, policy: Policy, path: Path) -> "DocumentPipeline":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(stages, policy, sqlite3.connect(str(path), check_same_thread=False))

    # The graph

    NODES: ClassVar[tuple[str, ...]] = (
        "scope",
        "approve_scope",
        "find",
        "approve_sources",
        "write",
        "decide",
        "audit",
        "review",
        "checked",
        "approve_publishing",
        "revise",
        "publish",
        "budget",
    )

    def build(self) -> StateGraph[DocumentRun]:
        graph = StateGraph(DocumentRun)
        for name in self.NODES:
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "scope")
        graph.add_conditional_edges("scope", self.after_paid("approve_scope"))
        graph.add_conditional_edges("approve_scope", self.after_approval("find"))
        graph.add_conditional_edges("find", self.after_paid("approve_sources"))
        graph.add_conditional_edges("approve_sources", self.after_approval("write"))
        graph.add_conditional_edges("write", self.after_write)
        graph.add_conditional_edges("decide", self.after_decision)
        graph.add_conditional_edges("audit", self.after_audit)
        graph.add_conditional_edges("review", self.after_paid("audit"))
        graph.add_conditional_edges("checked", self.after_paid("approve_publishing"))
        graph.add_conditional_edges("approve_publishing", self.after_publishing)
        graph.add_conditional_edges("revise", self.after_paid("audit"))
        graph.add_edge("publish", END)
        graph.add_conditional_edges("budget", self.after_budget)
        return graph

    # Stages (plain work, no pause)

    def scope(self, run: DocumentRun) -> Update:
        blocked = self.over(run, self.policy(run).estimates.scope, "scope")
        if blocked is not None:
            return blocked
        questions = self.apart(self._stages.sources.scope, run, self.policy(run))
        return {
            "stage": Stage.SCOPE,
            "blocked": None,
            "questions": questions,
            "notes": (*run.notes, f"questions planned: {len(questions)}"),
        }

    def find(self, run: DocumentRun) -> Update:
        blocked = self.over(run, self.policy(run).estimates.find, "find")
        if blocked is not None:
            return blocked
        listing = self.apart(self._stages.sources.find, run, self.policy(run))
        return {
            "stage": Stage.SOURCES,
            "blocked": None,
            "notes": (*run.notes, "sources found"),
            "listing": listing,
        }

    def write(self, run: DocumentRun) -> Update:
        words = run.request.words or self.policy(run).words
        blocked = self.over(run, self.policy(run).estimates.write(words), "write")
        if blocked is not None:
            return blocked
        written = self.apart(self._stages.writing.write, run, self.policy(run), self.left(run))
        if written.held is not None:
            return self.cut_short(run, "write", written.held)
        if written.document is None:
            raise RuntimeError("the writing left no sections; nothing to check or publish")
        note = f"written: {written.sections} sections" + (
            f", {len(written.failed)} failed" if written.failed else ""
        )
        return {
            "stage": Stage.WRITING,
            "blocked": None,
            "document": written.document,
            "sections": written.sections,
            "failed": written.failed,
            "notes": (*run.notes, note),
        }

    def audit(self, run: DocumentRun) -> Update:
        blocked = self.over(run, self.policy(run).estimates.audit, "audit")
        if blocked is not None:
            return blocked
        result = self.apart(self._stages.checking.audit, run, self.policy(run))
        if result.total == 0:
            raise RuntimeError("the audit found no cited statements; a review would have nothing to go on")
        return {
            "stage": Stage.CHECKING,
            "blocked": None,
            "audits": (*run.audits, result),
            "notes": (*run.notes, f"audit: {result.render()}"),
        }

    def review(self, run: DocumentRun) -> Update:
        need = self.policy(run).review_rounds * self.policy(run).estimates.review(run.sections)
        blocked = self.over(run, need, "review")
        if blocked is not None:
            return blocked
        written = self.apart(self._stages.checking.review, run, self.policy(run), self.left(run))
        if written.held is not None:
            return self.cut_short(run, "review", written.held)
        if written.document is None:
            raise RuntimeError("the review left no sections; nothing to check or publish")
        note = f"review {run.reviews + 1} done" + (
            f"; {len(written.failed)} fixes left unfinished" if written.failed else ""
        )
        return {
            "blocked": None,
            "document": written.document,
            "reviews": run.reviews + 1,
            "notes": (*run.notes, note),
        }

    def revise(self, run: DocumentRun) -> Update:
        """The changes the user asked for at the publishing pause, made by the section writers in one round;
        then audited again, looked at by the red team again, and back to the user."""
        revision = run.revision
        if revision is None:
            raise RuntimeError("nothing to revise: the run has no changes waiting")
        blocked = self.over(run, self.policy(run).estimates.review(run.sections), "revise")
        if blocked is not None:
            return blocked
        written = self.apart(self._stages.checking.revise, run, self.policy(run), self.left(run), revision)
        if written.held is not None:
            return self.cut_short(run, "revise", written.held)
        if written.document is None:
            raise RuntimeError("the revision left no sections; nothing to check or publish")
        notes = [
            f"your changes made: {len(revision.instructions)} instructions, {len(revision.findings)} findings"
            + (f"; {len(written.failed)} revisions left unfinished" if written.failed else "")
        ]
        notes += [f"not made, no section holds it: {u}" for u in written.unplaced]
        return {
            "stage": Stage.CHECKING,
            "blocked": None,
            "document": written.document,
            "revision": None,
            "revisions": run.revisions + 1,
            "notes": (*run.notes, *notes),
        }

    @staticmethod
    def cut_short(run: DocumentRun, step: Literal["write", "review", "revise"], held: str) -> Update:
        """A step a limit refused partway: the run waits at the budget question, to be retried once the
        limit allows (a machine-wide limit resets at midnight UTC), not carried on as if it were done."""
        return {
            "blocked": step,
            "need": 0.0,
            "held": held,
            "notes": (*run.notes, f"{step} stopped short: {held}"),
        }

    def checked(self, run: DocumentRun) -> Update:
        """Written and checked: the red team looks at the finished document, and the run waits to be published
        (its stage says so while it waits). A red team that fails doesn't stop the run: its findings are
        advice for the user, not a gate."""
        blocked = self.over(run, self.policy(run).estimates.attack, "checked")
        if blocked is not None:
            return blocked
        try:
            report = self.apart(self._stages.checking.attack, run, self.policy(run))
        except WorkerError as error:
            return {
                "stage": Stage.PUBLISHING,
                "blocked": None,
                "flaws": (),
                "notes": (*run.notes, f"the red team failed: {str(error)[:200]}"),
            }
        return {
            "stage": Stage.PUBLISHING,
            "blocked": None,
            "flaws": report.shown,
            "notes": (
                *run.notes,
                f"red team: {len(report.shown)} flaws shown, {len(report.unshown)} dropped",
            ),
        }

    def publish(self, run: DocumentRun) -> Update:
        page = self.apart(self._stages.publishing.publish, run, self.policy(run))
        return {"stage": Stage.PUBLISHED, "page": page, "notes": (*run.notes, f"page written: {page}")}

    @staticmethod
    def apart[**P, T](work: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Runs a stage in a fresh thread, outside the graph's context: LangGraph hands its configuration to
        whatever runs inside a node through context variables, and a model call (a LangChain agent, itself a
        LangGraph graph) then took the pipeline's checkpointer as its own and failed (2026-11-01, the first
        real run: "SqliteSaver does not support async methods"). A new thread starts with no context."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work, *args, **kwargs).result()

    # Pauses for the user

    def approve_scope(self, run: DocumentRun) -> Update:
        """The first pause: what the document will answer, in plain questions, before anything is searched. A
        misread topic is cheapest to catch here (moe-systems, 2026-11-22: noticed only in the document)."""
        listed = "\n".join(f"{n}. {q}" for n, q in enumerate(run.questions, 1))
        answer = self.ask(
            Question(
                title=self.SCOPE_QUESTION,
                details=f"The document will answer these questions, for {run.request.reader}:\n\n{listed}\n\n"
                "Untick any to leave out, add your own, then approve; if they miss what you meant, stop and "
                "start again in other words.",
                choices=(Choice.YES, Choice.STOP),
                items=run.questions,
            )
        )
        update = self.answered(run, answer)
        if answer.choice is Choice.YES and (answer.drop or answer.add):
            kept = tuple(q for n, q in enumerate(run.questions, 1) if n not in answer.drop)
            added = tuple(" ".join(q.split()) for q in answer.add if q.strip())
            update["questions"] = (*kept, *added)
            notes = [f"questions left out: {len(run.questions) - len(kept)}"] if answer.drop else []
            notes += [f"your questions added: {len(added)}"] if added else []
            update["notes"] = (*update["notes"], *notes)
        return update

    def approve_sources(self, run: DocumentRun) -> Update:
        spent = self._stages.spending.spent(run)
        words = run.request.words or self.policy(run).words
        rest = (
            self.policy(run).expected(words)
            - self.policy(run).estimates.scope
            - self.policy(run).estimates.find
        )
        answer = self.ask(
            Question(
                title=self.SOURCES_QUESTION,
                details=f"{run.listing}\nAdd, remove or swap files in {run.sources}, then approve. "
                f"Spent so far ${spent:.2f} of ${run.request.budget:.2f}; the rest is expected to cost "
                f"about ${rest:.2f}.",
                choices=(Choice.YES, Choice.STOP),
            )
        )
        return self.answered(run, answer)

    def decide(self, run: DocumentRun) -> Update:
        answer = self.ask(
            Question(
                title="Some sections failed their checks",
                details="\n".join(run.failed)
                + "\nretry: try them again; accept: take them as they are; drop: leave them out.",
                choices=(Choice.RETRY, Choice.ACCEPT, Choice.DROP, Choice.STOP),
            )
        )
        if answer.choice in (Choice.RETRY, Choice.ACCEPT, Choice.DROP):
            self.apart(self._stages.writing.settle, run, answer.choice)
        return self.answered(run, answer)

    def approve_publishing(self, run: DocumentRun) -> Update:
        audit = run.audits[-1].render() if run.audits else "not audited"
        spent = self._stages.spending.spent(run)
        issues = (
            ReferenceCheck.issues(run.document.read_text())
            if run.document is not None and run.document.is_file()
            else ()
        )
        flagged = "".join(f"\n- {i}" for i in issues)
        references = f"References to check ({len(issues)}):{flagged}\n" if issues else ""
        fixable = self.fixable(run)
        others = [f for f in run.flaws if f not in fixable]
        found = "".join(f"\n{n}. {item}" for n, item in enumerate(self.items(fixable), 1))
        listed = (
            f"The red team's findings, to fix unless you untick them ({len(fixable)}):{found}\n"
            if fixable
            else ""
        )
        remarks = "".join(f"\n- [{f.kind}] {f.where}: {f.flaw}" for f in others)
        noted = (
            f"The red team's other remarks, not for the writers ({len(others)}):{remarks}\n" if others else ""
        )
        answer = self.ask(
            Question(
                title=self.PUBLISHING_QUESTION,
                details=f"The document: {run.document}\nCitations: {audit}; fact reviews: {run.reviews}; "
                f"your rounds of changes: {run.revisions}.\n{listed}{noted}{references}"
                f"Cost: ${spent:.2f} of ${run.request.budget:.2f}.\n"
                "yes writes the page beside the run. revise has the writers make the ticked findings and "
                "your own instructions in one round (about "
                f"${self.policy(run).estimates.review(run.sections):.2f}), audits again and comes back here. "
                "An instruction quoting a passage in double quotes, or naming a section's title, is about "
                "that place; one naming neither is a rule for the whole document.",
                choices=(Choice.YES, Choice.REVISE, Choice.STOP),
                items=self.items(fixable),
                instructions=True,
            )
        )
        update = self.answered(run, answer)
        if answer.choice is Choice.REVISE:
            update["revision"] = Revision(
                findings=tuple(f for n, f in enumerate(fixable, 1) if n not in answer.drop),
                instructions=tuple(" ".join(i.split()) for i in answer.add if i.strip()),
            )
            update["stage"] = Stage.CHECKING
        return update

    @staticmethod
    def fixable(run: DocumentRun) -> tuple[Finding, ...]:
        """The red team's findings the writers can make: those of the text, not of how documents are made."""
        return tuple(f for f in run.flaws if f.kind is FlawKind.REVISE)

    @staticmethod
    def items(findings: tuple[Finding, ...]) -> tuple[str, ...]:
        return tuple(f'{f.where}: {f.flaw} ("{f.evidence[:160]}")' for f in findings)

    def budget(self, run: DocumentRun) -> Update:
        spent = self._stages.spending.spent(run)
        if run.held is not None:
            question = Question(
                title="A spending limit stopped the run",
                details=f"Spent ${spent:.2f} of ${run.request.budget:.2f}. The {run.blocked} step stopped "
                f"short: {run.held}. Retry it once the limit allows (a machine-wide limit resets at "
                "midnight UTC), raise the run's budget if that is the limit, or stop.",
                choices=(Choice.RETRY, Choice.BUDGET, Choice.STOP),
            )
        else:
            question = Question(
                title="The budget would run out",
                details=f"Spent ${spent:.2f} of ${run.request.budget:.2f}; the next step ({run.blocked}) is "
                f"expected to cost about ${run.need:.2f}. Raise the budget to go on, or stop.",
                choices=(Choice.BUDGET, Choice.STOP),
            )
        answer = self.ask(question)
        update = self.answered(run, answer)
        update["held"] = None
        if answer.choice is Choice.BUDGET and answer.budget is not None:
            update["request"] = run.request.model_copy(update={"budget": answer.budget})
        return update

    def ask(self, question: Question) -> Answer:
        return Answer.model_validate(interrupt(question.model_dump(mode="json")))

    def answered(self, run: DocumentRun, answer: Answer) -> Update:
        update: Update = {"answer": answer, "notes": (*run.notes, f"you answered {answer.choice}")}
        if answer.choice is Choice.STOP:
            update["stage"] = Stage.STOPPED
        return update

    # Routing

    def after_paid(self, then: str) -> Any:
        def route(run: DocumentRun) -> str:
            return "budget" if run.blocked else then

        return route

    def after_approval(self, then: str) -> Any:
        def route(run: DocumentRun) -> str:
            return END if run.stage is Stage.STOPPED else then

        return route

    def after_write(self, run: DocumentRun) -> str:
        if run.blocked:
            return "budget"
        return "decide" if run.failed else "audit"

    def after_publishing(self, run: DocumentRun) -> str:
        if run.stage is Stage.STOPPED:
            return END
        return "revise" if run.revision is not None else "publish"

    def after_decision(self, run: DocumentRun) -> str:
        if run.stage is Stage.STOPPED:
            return END
        return "write"  # retried sections run again; accepted or dropped ones let the project finish

    def after_audit(self, run: DocumentRun) -> str:
        if run.blocked:
            return "budget"
        # Every document is reviewed once, whatever its citations score: the reader review finds what no
        # citation check can, and a document with well-supported citations skipped all review before
        # (2026-11-13, docs/design/self-correction.md). Claims are checked too when the audit falls short.
        if self.policy(run).review_rounds and not run.reviews:
            return "review"
        return "checked"

    def after_budget(self, run: DocumentRun) -> str:
        if run.stage is Stage.STOPPED or run.blocked is None:
            return END
        return run.blocked

    # Budget

    def left(self, run: DocumentRun) -> float:
        return max(run.request.budget - self._stages.spending.spent(run), 0.0)

    def over(
        self,
        run: DocumentRun,
        need: float,
        step: Literal["scope", "find", "write", "audit", "review", "checked", "revise"],
    ) -> Update | None:
        if self.left(run) >= need:
            return None
        return {"blocked": step, "need": need, "notes": (*run.notes, f"paused before {step}: budget")}

    # The user's commands

    def config(self, name: str) -> Any:
        return {"configurable": {"thread_id": name}}

    def start(self, request: RunRequest, folder: Path) -> DocumentRun:
        if self.exists(request.name):
            raise ValueError(f"a run named {request.name!r} exists already: `agentswarm runs`")
        with Working(folder):
            self._graph.invoke(DocumentRun(request=request, folder=folder), self.config(request.name))
        return self.run(request.name)

    def check(self, name: str, answer: Answer) -> None:
        """Raises ValueError, saying why, when the run can't take this answer now; needs no model, so the
        studio answers at once before handing the step to the background."""
        if not self.exists(name):
            raise ValueError(f"no run named {name!r}")
        if self.running(name):
            raise ValueError(f"{name} is running a step now: answer it once that step ends")
        question = self.waiting(name)
        if question is None:
            if self.failed(name) is not None and answer.choice is Choice.RETRY:
                return
            raise ValueError(f"{name} isn't waiting for an answer")
        if answer.choice not in question.choices:
            raise ValueError(f"{name} can't take {answer.choice} now: {', '.join(question.choices)}")
        if answer.choice is Choice.BUDGET and answer.budget is None:
            raise ValueError("raising the budget needs the new total")
        if question.instructions:
            self.instructed(name, question, answer)
        elif answer.add and not question.items:
            raise ValueError(f"{name} isn't asking for questions now")
        # at the publishing, unticked findings matter only to revise (the studio sends them with any choice)
        ignored = question.instructions and answer.choice is not Choice.REVISE
        if answer.drop and not ignored:
            if not question.items:
                raise ValueError(f"{name} has nothing to leave out now")
            wrong = [n for n in answer.drop if not 1 <= n <= len(question.items)]
            item = "finding" if question.instructions else "question"
            if wrong:
                raise ValueError(f"no {item} numbered {wrong[0]}: they run from 1 to {len(question.items)}")
            if (
                answer.choice is Choice.YES
                and not question.instructions
                and len(set(answer.drop)) == len(question.items)
                and not answer.add
            ):
                raise ValueError("leaving out every question leaves nothing to write: stop the run instead")
        run = self.run(name)
        sourcing = question.title == self.SOURCES_QUESTION
        if sourcing and answer.choice is Choice.YES and not DocumentImport.files([run.sources]):
            raise ValueError(f"no documents in {run.sources} yet: add at least one, then approve")

    def instructed(self, name: str, question: Question, answer: Answer) -> None:
        """The publishing pause's answer: instructions go with revise, and a passage one quotes must be in the
        document word for word, or the writers couldn't find it either."""
        if answer.choice is Choice.YES and answer.add:
            raise ValueError("your instructions go with revise; yes publishes the document as it is")
        if answer.choice is not Choice.REVISE:
            return
        kept = len(question.items) - len(set(answer.drop) & set(range(1, len(question.items) + 1)))
        if not kept and not any(i.strip() for i in answer.add):
            raise ValueError("nothing to revise: keep a finding ticked or write an instruction")
        document = self.run(name).document
        text = document.read_text() if document is not None and document.is_file() else ""
        for instruction in answer.add:
            missing = UserRevisions.missing(instruction, text)
            if missing is not None:
                raise ValueError(
                    f'the passage "{missing[:80]}" isn\'t in the document word for word: copy it from the '
                    "document, or leave out the quotes to make it a rule for the whole document"
                )

    def answer(self, name: str, answer: Answer) -> DocumentRun:
        self.check(name, answer)
        with Working(self.run(name).folder):
            if self.waiting(name) is None:  # a retry of the step that failed
                self._graph.invoke(None, self.config(name))
            else:
                self._graph.invoke(Command(resume=answer.model_dump(mode="json")), self.config(name))
        return self.run(name)

    @staticmethod
    def current(run: DocumentRun, details: str) -> str:
        """The sources question with the reading list as the folder holds it now (papers fetched by hand
        since the run paused), not as it was found."""
        listed = DocumentImport.refresh(run.sources) if run.sources.is_dir() else None
        if listed is None or not run.listing or run.listing not in details:
            return details
        own = DocumentImport.unlisted(run.sources)
        added = f"\nAlso in the folder, added by you: {', '.join(f'`{n}`' for n in own)}\n" if own else ""
        return details.replace(run.listing, listed.render() + added)

    def failed(self, name: str) -> str | None:
        """Where and why a run stopped on an error (nothing is waiting, yet a step is still to run)."""
        snapshot = self._graph.get_state(self.config(name))
        if not snapshot.next or any(task.interrupts for task in snapshot.tasks):
            return None
        if self.running(name):
            return None  # in progress in another process, not stopped
        errors = [f"{task.name}: {task.error}" for task in snapshot.tasks if task.error]
        return errors[0] if errors else f"{snapshot.next[0]}: stopped before it finished"

    def running(self, name: str) -> bool:
        return Working(self.run(name).folder).active()

    def run(self, name: str) -> DocumentRun:
        snapshot = self._graph.get_state(self.config(name))
        if not snapshot.values:
            raise ValueError(f"no run named {name!r}")
        return DocumentRun.model_validate(snapshot.values)

    def waiting(self, name: str) -> Question | None:
        snapshot = self._graph.get_state(self.config(name))
        for task in snapshot.tasks:
            for paused in task.interrupts:
                return Question.model_validate(paused.value)
        return None

    def spent(self, name: str) -> float:
        return self._stages.spending.spent(self.run(name))

    def view(self, name: str) -> RunView:
        run, question = self.run(name), self.waiting(name)
        if question is not None and question.title == self.SOURCES_QUESTION:
            question = question.model_copy(update={"details": self.current(run, question.details)})
        return RunView(
            run=run,
            spent=self.spent(name),
            question=question,
            failure=self.failed(name),
            running=self.running(name),
        )

    def report(self, name: str) -> str:
        """The run for the user: where it is, what it cost, what happened, and what it waits for."""
        view = self.view(name)
        run, question, failure = view.run, view.question, view.failure
        lines = [
            f"{name}: {run.stage}, ${view.spent:.2f} of ${run.request.budget:.2f} spent",
            f"  topic: {run.request.topic}",
            *(f"  - {note}" for note in run.notes),
        ]
        if run.document is not None:
            lines.append(f"  document: {run.document}")
        if run.page is not None:
            lines.append(f"  page: {run.page}")
        if view.running:
            lines += ["", "RUNNING: a step is in progress; check again later (don't retry it)"]
        if failure is not None:
            lines += ["", f"STOPPED BY AN ERROR at {failure[:600]}", ""]
            lines.append(f"retry from that step: uv run agentswarm approve {name} --choice retry")
        if question is not None:
            lines += ["", f"WAITING: {question.title}", question.details, ""]
            lines.append(
                "answer: uv run agentswarm approve " + name + " --choice " + "|".join(question.choices)
            )
            if question.instructions:
                lines.append(
                    '  (with --choice revise: --drop N [N ...] unticks those findings, --add "..." adds an '
                    "instruction of yours)"
                )
            elif question.items:
                lines.append(
                    '  (with --choice yes: --drop N [N ...] leaves those out, --add "..." adds your own)'
                )
        return "\n".join(lines)

    def line(self, name: str) -> str:
        """One line per run, for `agentswarm runs`."""
        view = self.view(name)
        waits = f", waiting: {view.question.title.lower()}" if view.question is not None else ""
        if view.failure is not None:
            waits = ", stopped by an error (retry it)"
        if view.running:
            waits = ", running"
        run = view.run
        return f"{name:<28} {run.stage:<11} ${view.spent:.2f} of ${run.request.budget:.2f}{waits}"

    def exists(self, name: str) -> bool:
        return bool(self._graph.get_state(self.config(name)).values)

    def runs(self) -> list[str]:
        try:
            return [row[0] for row in self._db.execute(self.THREADS).fetchall()]
        except sqlite3.OperationalError:  # no run yet: the table doesn't exist
            return []

    def close(self) -> None:
        self._db.close()
