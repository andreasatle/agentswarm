# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false
"""The document run as a LangGraph graph (the user's choice, 2026-11-01: a learning goal, and the tool made
for a fixed workflow that pauses for a person). The graph only orders the stages and pauses; every stage is a
plain class in stages.py. Its checkpointer keeps each run in SQLite between the user's commands, so a run
waits for an approval for as long as it takes and survives a restart.

    find -> approve sources -> write -> (decide on failed sections) -> audit -> (review -> audit) ->
    checked -> approve publishing -> publish

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
from agentswarm.pipeline.policy import Policy
from agentswarm.pipeline.run import Answer, Choice, DocumentRun, Question, RunRequest, RunView, Stage
from agentswarm.pipeline.stages import Stages

type Update = dict[str, Any]


class DocumentPipeline:
    THREADS = "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
    SOURCES_QUESTION = "Approve the sources"
    # Our types in a saved run, named for the checkpointer's serializer (it warns about, and will refuse,
    # types it wasn't told about)
    SAVED: ClassVar[list[tuple[str, str]]] = [
        ("agentswarm.evaluation.papers", "PaperSource"),
        *(
            (
                "agentswarm.pipeline.run",
                name,
            )
            for name in ("RunRequest", "Stage", "Choice", "Answer", "AuditResult", "DocumentRun", "Question")
        ),
    ]

    def __init__(self, stages: Stages, policy: Policy, checkpoints: sqlite3.Connection) -> None:
        self._stages = stages
        self._policy = policy
        self._db = checkpoints
        self._saver = SqliteSaver(checkpoints, serde=JsonPlusSerializer(allowed_msgpack_modules=self.SAVED))
        self._graph = self.build().compile(checkpointer=self._saver)

    @classmethod
    def open(cls, stages: Stages, policy: Policy, path: Path) -> "DocumentPipeline":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(stages, policy, sqlite3.connect(str(path), check_same_thread=False))

    # The graph

    NODES: ClassVar[tuple[str, ...]] = (
        "find",
        "approve_sources",
        "write",
        "decide",
        "audit",
        "review",
        "checked",
        "approve_publishing",
        "publish",
        "budget",
    )

    def build(self) -> StateGraph[DocumentRun]:
        graph = StateGraph(DocumentRun)
        for name in self.NODES:
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "find")
        graph.add_conditional_edges("find", self.after_paid("approve_sources"))
        graph.add_conditional_edges("approve_sources", self.after_approval("write"))
        graph.add_conditional_edges("write", self.after_write)
        graph.add_conditional_edges("decide", self.after_decision)
        graph.add_conditional_edges("audit", self.after_audit)
        graph.add_conditional_edges("review", self.after_paid("audit"))
        graph.add_edge("checked", "approve_publishing")
        graph.add_conditional_edges("approve_publishing", self.after_approval("publish"))
        graph.add_edge("publish", END)
        graph.add_conditional_edges("budget", self.after_budget)
        return graph

    # Stages (plain work, no pause)

    def find(self, run: DocumentRun) -> Update:
        blocked = self.over(run, self._policy.estimates.find, "find")
        if blocked is not None:
            return blocked
        listing = self.apart(self._stages.sources.find, run, self._policy)
        return {
            "stage": Stage.SOURCES,
            "blocked": None,
            "notes": (*run.notes, "sources found"),
            "listing": listing,
        }

    def write(self, run: DocumentRun) -> Update:
        words = run.request.words or self._policy.words
        blocked = self.over(run, self._policy.estimates.write(words), "write")
        if blocked is not None:
            return blocked
        written = self.apart(self._stages.writing.write, run, self._policy, self.left(run))
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
        blocked = self.over(run, self._policy.estimates.audit, "audit")
        if blocked is not None:
            return blocked
        result = self.apart(self._stages.checking.audit, run, self._policy)
        if result.total == 0:
            raise RuntimeError("the audit found no cited statements; a review would have nothing to go on")
        return {
            "stage": Stage.CHECKING,
            "blocked": None,
            "audits": (*run.audits, result),
            "notes": (*run.notes, f"audit: {result.render()}"),
        }

    def review(self, run: DocumentRun) -> Update:
        blocked = self.over(run, self._policy.estimates.review(run.sections), "review")
        if blocked is not None:
            return blocked
        written = self.apart(self._stages.checking.review, run, self._policy, self.left(run))
        if written.document is None:
            raise RuntimeError("the review left no sections; nothing to check or publish")
        note = f"facts review {run.reviews + 1} done" + (
            f"; {len(written.failed)} fixes left unfinished" if written.failed else ""
        )
        return {
            "blocked": None,
            "document": written.document,
            "reviews": run.reviews + 1,
            "notes": (*run.notes, note),
        }

    def checked(self, run: DocumentRun) -> Update:
        """Written and checked: the run now waits to be published (its stage says so while it waits)."""
        return {"stage": Stage.PUBLISHING}

    def publish(self, run: DocumentRun) -> Update:
        page = self.apart(self._stages.publishing.publish, run, self._policy)
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

    def approve_sources(self, run: DocumentRun) -> Update:
        spent = self._stages.spending.spent(run)
        words = run.request.words or self._policy.words
        rest = self._policy.expected(words) - self._policy.estimates.find
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
        answer = self.ask(
            Question(
                title="Approve publishing",
                details=f"The document: {run.document}\nCitations: {audit}; fact reviews: {run.reviews}.\n"
                f"Cost: ${spent:.2f} of ${run.request.budget:.2f}.\n"
                "yes writes the page beside the run.",
                choices=(Choice.YES, Choice.STOP),
            )
        )
        return self.answered(run, answer)

    def budget(self, run: DocumentRun) -> Update:
        spent = self._stages.spending.spent(run)
        answer = self.ask(
            Question(
                title="The budget would run out",
                details=f"Spent ${spent:.2f} of ${run.request.budget:.2f}; the next step ({run.blocked}) is "
                f"expected to cost about ${run.need:.2f}. Raise the budget to go on, or stop.",
                choices=(Choice.BUDGET, Choice.STOP),
            )
        )
        update = self.answered(run, answer)
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

    def after_decision(self, run: DocumentRun) -> str:
        if run.stage is Stage.STOPPED:
            return END
        return "write"  # retried sections run again; accepted or dropped ones let the project finish

    def after_audit(self, run: DocumentRun) -> str:
        if run.blocked:
            return "budget"
        audit = run.audits[-1]
        if audit.share < self._policy.review_threshold and run.reviews < self._policy.review_rounds:
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
        self, run: DocumentRun, need: float, step: Literal["find", "write", "audit", "review"]
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
        self._graph.invoke(DocumentRun(request=request, folder=folder), self.config(request.name))
        return self.run(request.name)

    def check(self, name: str, answer: Answer) -> None:
        """Raises ValueError, saying why, when the run can't take this answer now; needs no model, so the
        studio answers at once before handing the step to the background."""
        if not self.exists(name):
            raise ValueError(f"no run named {name!r}")
        question = self.waiting(name)
        if question is None:
            if self.failed(name) is not None and answer.choice is Choice.RETRY:
                return
            raise ValueError(f"{name} isn't waiting for an answer")
        if answer.choice not in question.choices:
            raise ValueError(f"{name} can't take {answer.choice} now: {', '.join(question.choices)}")
        if answer.choice is Choice.BUDGET and answer.budget is None:
            raise ValueError("raising the budget needs the new total")
        run = self.run(name)
        sourcing = question.title == self.SOURCES_QUESTION
        if sourcing and answer.choice is Choice.YES and not DocumentImport.files([run.sources]):
            raise ValueError(f"no documents in {run.sources} yet: add at least one, then approve")

    def answer(self, name: str, answer: Answer) -> DocumentRun:
        self.check(name, answer)
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
        return details.replace(run.listing, listed.render())

    def failed(self, name: str) -> str | None:
        """Where and why a run stopped on an error (nothing is waiting, yet a step is still to run)."""
        snapshot = self._graph.get_state(self.config(name))
        if not snapshot.next or any(task.interrupts for task in snapshot.tasks):
            return None
        errors = [f"{task.name}: {task.error}" for task in snapshot.tasks if task.error]
        return errors[0] if errors else f"{snapshot.next[0]}: stopped before it finished"

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
        return RunView(run=run, spent=self.spent(name), question=question, failure=self.failed(name))

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
        if failure is not None:
            lines += ["", f"STOPPED BY AN ERROR at {failure[:600]}", ""]
            lines.append(f"retry from that step: uv run agentswarm approve {name} --choice retry")
        if question is not None:
            lines += ["", f"WAITING: {question.title}", question.details, ""]
            lines.append(
                "answer: uv run agentswarm approve " + name + " --choice " + "|".join(question.choices)
            )
        return "\n".join(lines)

    def line(self, name: str) -> str:
        """One line per run, for `agentswarm runs`."""
        view = self.view(name)
        waits = f", waiting: {view.question.title.lower()}" if view.question is not None else ""
        if view.failure is not None:
            waits = ", stopped by an error (retry it)"
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
