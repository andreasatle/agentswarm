"""The prompt catalogue: every model call and every Jev question, as the model sees it, generated from code.

The instructions, payloads, tools, answers and questions live in the classes that use them. This module only
collects them, so the catalogue can't drift from what runs: `agentswarm prompts` prints it, and
docs/PROMPTS.md is its output, kept current by a test.

Each payload is shown filled with sample data (greeter-like), rendered exactly as a worker sends it.
"""

import inspect
import json
import types
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Self, Union, get_args, get_origin

from pydantic import BaseModel

from agentswarm.core.domain import (
    ArtifactId,
    AttackFocus,
    Finding,
    FindingId,
    FindingKind,
    Instruction,
    Level,
    Severity,
    TeamName,
)
from agentswarm.evaluation.probe import (
    AcceptAnswer,
    AcceptRequest,
    CritiqueAnswer,
    CritiqueRequest,
    Critiquing,
    ProduceAnswer,
    ProduceRequest,
    Producing,
    ProjectFiles,
    SourceFile,
)
from agentswarm.jev.spec import QuestionSet
from agentswarm.judging.deciders import LLMAnswers, LLMDecider, LLMQuestions
from agentswarm.judging.judges import ChangeReadiness, Judge, JudgeCatalog, JudgeState
from agentswarm.llm.workers import LangChainWorker, Prompts
from agentswarm.teams.acceptance import AcceptanceTester
from agentswarm.teams.analyzer import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisToolbox,
    ContractTester,
    EdgeValueTester,
    ErrorTester,
    IntegrationTester,
    ProbingAnalyzer,
    ReadingToolbox,
    RedTeam,
    ReviewingAnalyzer,
    ReviewReport,
    TextTester,
)
from agentswarm.teams.decomposer import Decomposer, Split, SplitRequest
from agentswarm.teams.documents import AudienceReviewer, ConsistencyChecker, FactChecker, Outliner, Writer
from agentswarm.teams.executor import ExecutorReport, ExecutorRequest, ExecutorTeam, WorktreeToolbox
from agentswarm.teams.hardening import AttackReport, AttackRequest, AttackToolbox, Hardener
from agentswarm.teams.referee import (
    ConflictingTest,
    ReconsiderAnswer,
    ReconsiderRequest,
    Referee,
    ReportAnswer,
    ReportRequest,
    RulingAnswer,
    RulingRequest,
)
from agentswarm.teams.stateless import BuildState, BuildStep, Outcome, StatelessBuilder

type ToolboxClass = type[WorktreeToolbox] | type[AnalysisToolbox] | type[ReadingToolbox]


@dataclass(frozen=True)
class Caller:
    """One team (or judge) that makes a family's call, with its own instructions."""

    use: str  # the team name a project configuration uses (`use:`), or "" for a judge fallback
    owner: type  # the class that holds the instructions
    instructions: str


@dataclass(frozen=True)
class CallFamily:
    """Calls that share a payload, tools, answer and handling, and differ only in their instructions."""

    title: str
    callers: tuple[Caller, ...]
    handler: type  # turns the answer into events; its docstring says what happens to the answer
    payload: BaseModel  # sample data, rendered as the worker sends it
    output: type[BaseModel]
    toolbox: ToolboxClass | None  # its TOOLS name the methods the model may call

    def uses(self) -> tuple[str, ...]:
        return tuple(c.use for c in self.callers if c.use)


class SampleData:
    """Plausible payloads for the catalogue: a greeter project on its second attempt at a task."""

    NORTH_STAR = "A small greeting library"
    TASK = "Add farewell(name) that returns 'Goodbye, <name>!' and raises ValueError for an empty name."
    DIFF = (
        "--- a/greeter.py\n+++ b/greeter.py\n@@ -3,0 +4,5 @@\n+def farewell(name: str) -> str:\n"
        '+    if not name:\n+        raise ValueError("name must not be empty")\n'
        '+    return f"Goodbye, {name}!"\n'
    )

    @classmethod
    def finding(cls) -> Finding:
        return Finding(
            id=FindingId("f-0123456789ab"),
            artifact=ArtifactId("a-0001"),
            level=Level("raw"),
            team=TeamName("gate"),
            kind=FindingKind.INCOMPLETE,
            severity=Severity.MAJOR,
            detail="The tests do not exercise the behaviour the task describes",
        )

    @classmethod
    def executor(cls) -> ExecutorRequest:
        instruction = Instruction(
            goal=cls.TASK, must_fix=(cls.finding(),), constraints=("Use only the standard library.",)
        )
        return ExecutorRequest(
            north_star=cls.NORTH_STAR,
            instruction=instruction,
            files=("greeter.py", "tests/test_greeter.py"),
            diff_summary=" greeter.py | 5 +++++\n 1 file changed, 5 insertions(+)",
        )

    @classmethod
    def build_state(cls) -> BuildState:
        request = cls.executor()
        read = Outcome(
            request="read greeter.py 1-300", result="    1| def hello(name: str) -> str:\n    2| ..."
        )
        return BuildState(
            north_star=request.north_star,
            instruction=request.instruction,
            files=request.files,
            round=2,
            rounds_left=18,
            notes="farewell is missing from greeter.py; add it after hello, then run the tests.",
            outcomes=(read,),
            checks="unit_tests: FAILED\n1 failed: test_farewell",
        )

    @classmethod
    def analysis(cls) -> AnalysisRequest:
        return AnalysisRequest(north_star=cls.NORTH_STAR, task=cls.TASK, diff=cls.DIFF)

    @classmethod
    def split(cls) -> SplitRequest:
        task = "Add a greeting book: store greetings per contact, look them up, and export them as CSV."
        return SplitRequest(north_star=cls.NORTH_STAR, task=task, depth=0)

    @classmethod
    def judge_state(cls) -> JudgeState:
        return JudgeState(
            north_star=cls.NORTH_STAR, task=cls.TASK, must_fix=(cls.finding().detail,), diff=cls.DIFF
        )

    SEED = 'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n'
    SEED_TEST = 'from greeter import greet\n\n\ndef test_greet():\n    assert greet("Ada") == "Hello, Ada!"\n'

    @classmethod
    def produce(cls) -> ProduceRequest:
        files = (
            SourceFile(path="greeter.py", content=cls.SEED),
            SourceFile(path="tests/test_greeter.py", content=cls.SEED_TEST),
        )
        constraints = ("Use only the Python standard library.",)
        return ProduceRequest(north_star=cls.NORTH_STAR, task=cls.TASK, constraints=constraints, files=files)

    @classmethod
    def accept(cls) -> AcceptRequest:
        request = cls.produce()
        return AcceptRequest(north_star=request.north_star, task=request.task, files=request.files)

    @classmethod
    def attack(cls) -> AttackRequest:
        return AttackRequest(
            north_star=cls.NORTH_STAR,
            section="Greeting",
            passage="## Greeting\n\ngreet(name) returns 'Hello, <name>!'; an empty name raises ValueError.",
            focus=Hardener.FOCUS[AttackFocus.BOUNDARIES],
        )

    @classmethod
    def report(cls) -> ReportRequest:
        test = (
            "from greeter import greet\nimport pytest\n\n\n"
            "def test_empty():\n    with pytest.raises(ValueError):\n        greet('')\n"
        )
        return ReportRequest(
            claim="greet('') returns 'Hello, !' instead of raising ValueError",
            test_file=test,
            quote="an empty name raises ValueError",
            passage="## Greeting greet(name) returns 'Hello, <name>!'; an empty name raises ValueError.",
        )

    @classmethod
    def reconsider(cls) -> ReconsiderRequest:
        report = cls.report()
        existing = "CASES = [\n    ('empty-name', '', 'Hello, guest!'),\n]\n"
        return ReconsiderRequest(
            claim=report.claim,
            test_file=report.test_file,
            quote=report.quote,
            passage=report.passage,
            conflicts=(
                ConflictingTest(
                    test="tests/acceptance/test_greet.py::test_greet[empty-name]", excerpt=existing
                ),
            ),
        )

    @classmethod
    def ruling(cls) -> RulingRequest:
        test = "from greeter import greet\n\n\ndef test_greet():\n    assert greet('Ada') == 'Hi, Ada!'\n"
        return RulingRequest(
            test="tests/acceptance/test_a_0001.py::test_greet",
            test_file=test,
            quote="greet(name) returns 'Hello, <name>!'",
            passage="# Greeter greet(name) returns 'Hello, <name>!' for any name.",
            reason="the spec says Hello, the test demands Hi",
        )

    @classmethod
    def critique(cls) -> CritiqueRequest:
        farewell = (  # the planted bug: `if name` should be `if not name`
            "def farewell(name: str) -> str:\n"
            "    if name:\n"
            '        raise ValueError("name must not be empty")\n'
            '    return f"Goodbye, {name}!"\n'
        )
        changed = f"{cls.SEED}\n\n{farewell}"
        files = (
            SourceFile(path="greeter.py", content=changed),
            SourceFile(path="tests/test_greeter.py", content=cls.SEED_TEST),
        )
        diff = ProjectFiles.diff({"greeter.py": cls.SEED}, {"greeter.py": changed})
        return CritiqueRequest(north_star=cls.NORTH_STAR, task=cls.TASK, files=files, diff=diff)

    @classmethod
    def llm_questions(cls) -> LLMQuestions:
        questions = {q.field: q.ask.instructions for q in Judge(ChangeReadiness).questions}
        state = cls.judge_state().model_dump(mode="json")
        return LLMQuestions(questions=questions, state=state)


class PromptCatalogue:
    """Renders every model call and every Jev question as one Markdown document."""

    DOCUMENT = "docs/PROMPTS.md"
    REGENERATE = "uv run agentswarm prompts > docs/PROMPTS.md"
    NOT_MODEL_TEAMS: ClassVar[frozenset[str]] = frozenset({"fake_executor"})  # teams that call no model

    def __init__(self, families: Sequence[CallFamily], judges: Sequence[Judge]) -> None:
        self.families = tuple(families)
        self.judges = tuple(judges)

    @classmethod
    def standard(cls) -> Self:
        analysis = SampleData.analysis()
        executor = SampleData.executor()
        split = SampleData.split()
        families = [
            CallFamily(
                "Producers: executor and writer",
                (
                    Caller("executor", ExecutorTeam, ExecutorTeam.INSTRUCTIONS),
                    Caller("writer", Writer, Writer.INSTRUCTIONS),
                ),
                ExecutorTeam,
                executor,
                ExecutorReport,
                WorktreeToolbox,
            ),
            CallFamily(
                "Stateless builder: plain code runs the loop, one typed answer per round",
                (Caller("stateless_builder", StatelessBuilder, StatelessBuilder.STEP_INSTRUCTIONS),),
                StatelessBuilder,
                SampleData.build_state(),
                BuildStep,
                None,
            ),
            CallFamily(
                "Planners: decomposer and outliner",
                (
                    Caller("decomposer", Decomposer, Decomposer.INSTRUCTIONS),
                    Caller("outliner", Outliner, Outliner.INSTRUCTIONS),
                ),
                Decomposer,
                split,
                Split,
                None,
            ),
            CallFamily(
                "Probing analyzers: evidence is a failing test",
                (
                    Caller("integration_tester", IntegrationTester, IntegrationTester.INSTRUCTIONS),
                    Caller("red_team", RedTeam, RedTeam.INSTRUCTIONS),
                    Caller("edge_value_tester", EdgeValueTester, EdgeValueTester.INSTRUCTIONS),
                    Caller("text_tester", TextTester, TextTester.INSTRUCTIONS),
                    Caller("error_tester", ErrorTester, ErrorTester.INSTRUCTIONS),
                    Caller("contract_tester", ContractTester, ContractTester.INSTRUCTIONS),
                ),
                ProbingAnalyzer,
                analysis,
                AnalysisReport,
                AnalysisToolbox,
            ),
            CallFamily(
                "Reviewing analyzers: evidence is a verbatim quote",
                (
                    Caller("consistency_checker", ConsistencyChecker, ConsistencyChecker.INSTRUCTIONS),
                    Caller("fact_checker", FactChecker, FactChecker.INSTRUCTIONS),
                    Caller("audience_reviewer", AudienceReviewer, AudienceReviewer.INSTRUCTIONS),
                ),
                ReviewingAnalyzer,
                analysis,
                ReviewReport,
                ReadingToolbox,
            ),
            CallFamily(
                "Judge fallback: a model answering Jev's questions",
                (Caller("", LLMDecider, LLMDecider.INSTRUCTIONS),),
                LLMDecider,
                SampleData.llm_questions(),
                LLMAnswers,
                None,
            ),
            CallFamily(
                "Probe: produce in one answer (benchmark)",
                (Caller("", Producing, Producing.INSTRUCTIONS),),
                Producing,
                SampleData.produce(),
                ProduceAnswer,
                None,
            ),
            CallFamily(
                "Acceptance tester: tests written before the work (also its benchmark probe)",
                (
                    Caller("acceptance_tester", AcceptanceTester, AcceptanceTester.INSTRUCTIONS),
                    Caller("", AcceptanceTester, AcceptanceTester.DERIVED),  # `derived: true`: no labels
                ),
                AcceptanceTester,
                SampleData.accept(),
                AcceptAnswer,
                None,
            ),
            CallFamily(
                "Red team (hardening): attacks the finished product, one spec section and focus at a time",
                (Caller("", Hardener, Hardener.INSTRUCTIONS),),
                Hardener,
                SampleData.attack(),
                AttackReport,
                AttackToolbox,
            ),
            CallFamily(
                "Referee: confirms a red team's report against the spec before it becomes work",
                (Caller("", Referee, Referee.CONFIRM),),
                Referee,
                SampleData.report(),
                ReportAnswer,
                None,
            ),
            CallFamily(
                "Referee: looks again at a confirmed report whose fix broke existing protected tests",
                (Caller("", Referee, Referee.RECONSIDER),),
                Referee,
                SampleData.reconsider(),
                ReconsiderAnswer,
                None,
            ),
            CallFamily(
                "Referee: rules on an acceptance test the builder disputes (projects without labels)",
                (Caller("", Referee, Referee.INSTRUCTIONS),),
                Referee,
                SampleData.ruling(),
                RulingAnswer,
                None,
            ),
            CallFamily(
                "Probe: critique in one answer (benchmark)",
                (Caller("", Critiquing, Critiquing.INSTRUCTIONS),),
                Critiquing,
                SampleData.critique(),
                CritiqueAnswer,
                None,
            ),
        ]
        return cls(families, JudgeCatalog.standard().judges())

    def render(self) -> str:
        parts = [self._overview(), self._index(), *(self._family(f) for f in self.families), self._jev()]
        return "\n\n".join(parts) + "\n"

    def _overview(self) -> str:
        system_example = Prompts.system("<the team's instructions>")
        return f"""\
# Prompt catalogue

Generated from the code by `agentswarm prompts`; do not edit by hand. Regenerate with \
`{self.REGENERATE}` (a test fails when this file is out of date).

Every model call in AgentSwarm, and every question asked of Jev, as the model sees it. Calls that share \
a payload, tools and answer are shown as one family; only their instructions differ.

## How every model call is made

A worker (`llm/workers.py`) makes each call the same way:

1. **System prompt:** the team's instructions, then one fixed rule:

   ```text
{self._indent(system_example, "   ")}
   ```

2. **User message:** exactly one typed payload (a Pydantic model), serialized as JSON between `<data>` and \
`</data>`. Never free text. Each family below shows a sample.
3. **Tools** (for calls that have them): methods whose signature and docstring are all the model sees of \
them. A tool returns errors as text, so the model can correct itself.
4. **Answer:** the model must answer with the output model, through the provider's structured output (or a \
tool call where that is missing). The model sees the JSON schema Pydantic derives: field names, types and \
the class docstring, **not** the `#` comments in the source shown below.
5. **When no valid answer comes:** if the agent ends without one (local models often finish in plain \
text), the worker asks for the answer alone, up to {LangChainWorker.FINAL_ANSWER_TRIES} times:

   ```text
   {LangChainWorker.FINAL_ANSWER}
   ```

   If that fails too, or the provider fails, the call raises `WorkerError` and the team escalates (a \
reviewer's failure is a notice that doesn't park the artifact). A run stops after the team's \
`max_model_calls` model calls (40 unless the team configuration says otherwise)."""

    def _index(self) -> str:
        rows = ["| Family | Team names (`use:`) | Answer |", "| --- | --- | --- |"]
        for family in self.families:
            uses = ", ".join(f"`{u}`" for u in family.uses()) or f"(`{family.handler.__name__}`)"
            link = f"[{family.title}](#{self._anchor(family.title)})"
            rows.append(f"| {link} | {uses} | `{family.output.__name__}` |")
        rows.append(
            "| [Jev questions](#jev-questions) | gates, relevance filters, planners, router | answers |"
        )
        return "## Calls\n\n" + "\n".join(rows)

    def _family(self, family: CallFamily) -> str:
        sections = [f"## {family.title}"]
        for caller in family.callers:
            heading = f"### `{caller.use}`" if caller.use else f"### {caller.owner.__name__}"
            own = self._doc(caller.owner)
            where = f"`{caller.owner.__module__}.{caller.owner.__qualname__}`"
            about = f"{where}: {own}" if own else where
            sections.append(f"{heading}\n\n{about}\n\n```text\n{caller.instructions}\n```")
        sections += [
            "### User message (sample)\n\n```text\n" + Prompts.data(family.payload) + "\n```",
            "### Payload types\n\n" + self._sources(type(family.payload)),
        ]
        if family.toolbox is not None:
            sections.append("### Tools\n\n" + self._tools(family.toolbox))
        sections.append(
            "### Answer\n\n" + self._sources(family.output) + "\n\n" + self._schema(family.output)
        )
        handling = self._doc(family.handler)
        sections.append(f"### What happens to the answer\n\n`{family.handler.__qualname__}`: {handling}")
        return "\n\n".join(sections)

    def _jev(self) -> str:
        state = SampleData.judge_state().model_dump_json(indent=2)
        questions = QuestionSet.of(ChangeReadiness).model_dump_json(indent=2, exclude_none=True)
        judges = "\n\n".join(self._judge(j) for j in self.judges)
        return f"""\
## Jev questions

Jev answers typed questions about a typed state; it returns a probability per question, never text. Every \
judge asks yes/no questions phrased so that **yes is good**. A judge's version is a hash of its wording: \
new wording starts new calibration data, so a reworded question has to earn its thresholds again.

Every judge is shown the same state:

{self._sources(JudgeState)}

A sample state, as sent:

```json
{state}
```

The questions are sent built from the judge's fields. For `ChangeReadiness`:

```json
{questions}
```

{judges}"""

    def _judge(self, judge: Judge) -> str:
        doc = self._doc(judge.spec)
        lines = [
            f"### {judge.name}",
            f"Version `{judge.version}`." + (f" {doc}" if doc else ""),
        ]
        for q in judge.questions:
            entry = f"- **{q.field}**: {q.ask.instructions}"
            if q.ask.yes or q.ask.no:
                entry += f" Yes means: {q.ask.yes or '-'}. No means: {q.ask.no or '-'}."
            if q.fails is not None:
                entry += f' A clear no becomes a finding of kind `{q.fails.kind}`: "{q.fails.message}".'
            lines.append(entry)
        return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])

    def _tools(self, toolbox: ToolboxClass) -> str:
        names = toolbox.TOOLS
        entries: list[str] = []
        for name in names:
            method = getattr(toolbox, name)
            signature = inspect.signature(method)
            parameters = list(signature.parameters.values())[1:]  # without self
            shown = signature.replace(parameters=parameters)
            doc = inspect.getdoc(method) or ""
            entries.append(f"- `{name}{shown}`: {self._paragraphs(doc)}")
        return f"`{toolbox.__qualname__}`:\n\n" + "\n".join(entries)

    def _sources(self, model: type[BaseModel]) -> str:
        blocks = [inspect.getsource(m).rstrip() for m in self.models_in(model)]
        return "```python\n" + "\n\n\n".join(blocks) + "\n```"

    @staticmethod
    def _schema(output: type[BaseModel]) -> str:
        schema = json.dumps(output.model_json_schema(), indent=2)
        return f"<details><summary>JSON schema</summary>\n\n```json\n{schema}\n```\n\n</details>"

    @classmethod
    def models_in(cls, model: type[BaseModel]) -> list[type[BaseModel]]:
        """The model and every model its fields contain, outermost first."""
        found: list[type[BaseModel]] = [model]
        for field in model.model_fields.values():
            for inner in cls.types_in(field.annotation):
                if isinstance(inner, type) and issubclass(inner, BaseModel) and inner not in found:
                    found += [m for m in cls.models_in(inner) if m not in found]
        return found

    @classmethod
    def types_in(cls, annotation: Any) -> list[Any]:
        """The types an annotation mentions: tuple[X, ...] and X | None give X."""
        origin = get_origin(annotation)
        if origin is None:
            return [annotation]
        if origin in (Union, types.UnionType) or get_args(annotation):
            return [t for arg in get_args(annotation) for t in cls.types_in(arg)]
        return []

    @classmethod
    def _doc(cls, owner: type) -> str:
        """The class's own docstring as Markdown; an inherited one (say, BaseModel's) is not its own."""
        own = owner.__dict__.get("__doc__")
        return cls._paragraphs(inspect.cleandoc(own)) if isinstance(own, str) else ""

    @staticmethod
    def _paragraphs(doc: str) -> str:
        """A docstring as Markdown: line breaks inside a paragraph joined, paragraphs kept."""
        return "\n\n".join(" ".join(p.split("\n")) for p in doc.split("\n\n"))

    @staticmethod
    def _indent(text: str, prefix: str) -> str:
        return "\n".join(prefix + line if line else line for line in text.split("\n"))

    @staticmethod
    def _anchor(title: str) -> str:
        """GitHub's heading anchor: lower case, punctuation dropped, spaces as hyphens."""
        kept = "".join(c for c in title.lower() if c.isalnum() or c in " -_")
        return kept.replace(" ", "-")
