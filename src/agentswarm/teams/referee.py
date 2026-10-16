"""The referee: rules when a builder disputes an acceptance test against the spec.

See docs/design/self-supervised.md. In a project without labels the acceptance tests are model-written, and
published studies find a large share of model-written assertions wrong (28-58%). A builder can't change
acceptance tests, so a wrong one would block good work for good. Instead the builder may dispute one, quoting
the spec. Plain code checks first that the test is an acceptance test and that the quote is the spec's own
words; then a model of another family than the builder and the check writer reads the passage and the test
and rules. Upheld: plain code skips the test, with the ruling as the reason, in a commit of its own. Every
ruling is a `CheckDisputed` event, so wrong checks are counted.
"""

import ast
from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.domain import ArtifactId, CheckDisputed
from agentswarm.llm.workers import Worker, WorkerError
from agentswarm.workspace.git import Worktree


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Dispute(Frozen):
    """An acceptance test the builder believes contradicts the spec."""

    test: str = Field(description="the test's pytest id: tests/acceptance/<file>.py::<test function>")
    quote: str = Field(description="the spec's exact words the test contradicts, copied from the spec file")
    reason: str = Field(description="how the test contradicts that passage")


class RulingRequest(Frozen):
    """The payload a referee receives."""

    test: str  # the disputed test's pytest id
    test_file: str  # the whole test file, helpers included
    quote: str
    passage: str  # the spec around the quote, whitespace collapsed
    reason: str  # the builder's argument


class RulingAnswer(Frozen):
    """Your ruling on one disputed test: first your reasoning, then the verdict it leads to."""

    # Reasoning before the verdict: with the verdict first, a model commits before it thinks (the first run
    # had 4 of 7 rulings whose reasons agreed with the builder and whose verdicts didn't).
    reason: str = Field(description="what the spec requires for the input the test checks, citing it")
    upheld: bool = Field(
        description="true if the test contradicts the spec and must go; false if it is right"
    )


class ReportRequest(Frozen):
    """The payload a referee receives for a red team's report."""

    claim: str  # what the red team says the product gets wrong
    test_file: str  # the red team's test, which fails on the product
    quotes: tuple[str, ...]  # the spec's words it rests on: a combination of features may need several
    passage: str  # the spec around each quote, whitespace collapsed


class ReportAnswer(Frozen):
    """Your ruling on one red team report: first your reasoning, then the verdict it leads to."""

    reason: str = Field(description="what the spec requires for the input the test checks, citing it")
    confirmed: bool = Field(
        description="true only if the spec clearly requires exactly what the test asserts; false otherwise"
    )


class ConflictingTest(Frozen):
    """A protected test the product passed, broken by a change that made a red team's test pass."""

    test: str  # its pytest id
    excerpt: str  # its source around the case


class ReconsiderRequest(Frozen):
    """The payload a referee receives to look again at a report it confirmed."""

    claim: str
    test_file: str  # the red team's test
    quotes: tuple[str, ...]
    passage: str  # the spec around each quote, whitespace collapsed
    conflicts: tuple[ConflictingTest, ...]


class ReconsiderAnswer(Frozen):
    """Your second ruling on one red team report: first your reasoning, then the verdict it leads to."""

    reason: str = Field(
        description="whether a correct product could pass both the red team's test and the existing tests, "
        "and which of them the spec, read with the existing tests, supports"
    )
    withdrawn: bool = Field(
        description="true if the red team's test and the existing tests can't both hold and the existing "
        "tests show what the spec means; false if the report stands"
    )


class Referee:
    FOLDER = "tests/acceptance"
    CONTEXT = 1_500  # characters of spec on either side of the quote
    FILE_LIMIT = 40_000  # characters of the test file given to the model
    MAX_PER_ANSWER: ClassVar[int] = 3  # disputes ruled from one builder answer; more are ignored
    VOTES: ClassVar[int] = 3  # independent rulings per dispute; the majority decides (self-consistency)
    INSTRUCTIONS = """\
You are the referee between a builder and the checks someone else wrote for its work, in a project that has a
specification but no tests with known answers. The builder says one test contradicts the specification and
quotes the passage. Rule on that test alone, from the specification, in a single answer.

- Read the passage and the test (its file holds the helpers it uses). Decide what the specification requires
  for the input the test checks, and whether the test demands exactly that.
- Uphold the dispute only if the test demands something the specification contradicts or doesn't require.
  If the test is right, or the specification is unclear, the test stands.
- Don't judge the builder's code: it isn't shown, and doesn't matter here."""

    CONFIRM = """\
You are the referee for a red team that attacks a finished product against its specification. The red team
says the product gets one thing wrong, shows a test that fails on the product, and quotes the passage the
test rests on. Rule on that report alone, from the specification, in a single answer.

- Read the passage and the test. Decide what the specification requires for the input the test checks.
- Confirm only if the specification clearly requires exactly what the test asserts. If the test asserts
  something the specification doesn't say, or the specification is unclear, the report is not confirmed:
  a confirmed report becomes work for the builders.
- Don't judge the product's code: it isn't shown, and doesn't matter here."""

    RECONSIDER = """\
You are the referee for a red team that attacks a finished product against its specification. You confirmed
one of its reports before: it became work for the builders, with the red team's test as the acceptance test.
Every builder has failed that work, and a change that made the red team's test pass broke existing protected
tests that the product passed. Rule again on that report alone, in a single answer.

- Read the passage, the red team's test and the existing tests that broke.
- Decide first whether a correct product could pass both the red team's test and the existing tests. If it
  could, the builders' change was wrong, not the report: the report stands.
- If they can't both hold, decide which the specification supports. The existing tests are part of the
  project's definition of done: where the specification is brief or imprecise, they show what it means.
  Withdraw the report unless the specification clearly contradicts the existing tests.
- Don't judge the builders' code: it isn't shown, and doesn't matter here."""
    EXCERPT_LINES = 12  # lines of a broken test's file on either side of its case
    EXCERPT_LIMIT = 3_000  # characters of each broken test given to the model
    MAX_CONFLICTS = 5

    def __init__(self, worker: Worker, model: str, spec: Sequence[str]) -> None:
        self._worker = worker
        self._model = model
        self._spec = tuple(spec)  # files in the project, relative to its root
        # Rulings so far in this run, by test: a test ruled once stays ruled, in every step that meets it
        self._rulings: dict[str, tuple[bool, str]] = {}

    async def rule(
        self, worktree: Worktree, artifact: ArtifactId, dispute: Dispute, builder: str | None
    ) -> CheckDisputed | None:
        """The ruling, recorded; an upheld one has skipped the test in a commit. None: no ruling was had."""
        path, _, rest = dispute.test.partition("::")
        function = rest.split("[", 1)[0]
        test = f"{path}::{function}"

        def ruled(upheld: bool, ruling: str, referee: str | None) -> CheckDisputed:
            return CheckDisputed(
                artifact=artifact,
                test=test,
                quote=dispute.quote,
                reason=dispute.reason,
                model=builder,
                referee=referee,
                upheld=upheld,
                ruling=ruling,
            )

        source = self.source(worktree, path)
        definition = self.definition(source, function) if source is not None else None
        if source is None or definition is None:
            return ruled(False, f"{test} is not an acceptance test of this project", None)
        if test in self._rulings:  # ruled before in this run: the same ruling, applied here too
            upheld, reason = self._rulings[test]
            skipped = any(ast.unparse(d).startswith("pytest.mark.skip") for d in definition.decorator_list)
            if upheld and not skipped:
                worktree.write(path, self.skipped(source, function, reason))
                worktree.commit_paths(f"{artifact}: referee skips {test} (ruled before): {reason}", [path])
            return ruled(upheld, f"as ruled before: {reason}", self._model)
        passage = self.passage(worktree, dispute.quote)
        if passage is None:
            return ruled(False, "the quote is not in the spec, word for word", None)
        request = RulingRequest(
            test=test,
            test_file=source[: self.FILE_LIMIT],
            quote=dispute.quote,
            passage=passage,
            reason=dispute.reason,
        )
        answers: list[RulingAnswer] = []
        for _ in range(self.VOTES):
            try:
                answers.append(await self._worker.run(self.INSTRUCTIONS, request, RulingAnswer))
            except WorkerError:
                continue
            if self.decided(answers) is not None:
                break
        verdict = self.decided(answers)
        if verdict is None:
            return None
        answer = next(a for a in answers if a.upheld == verdict)
        self._rulings[test] = (answer.upheld, answer.reason)
        if answer.upheld:
            worktree.write(path, self.skipped(source, function, answer.reason))
            worktree.commit_paths(f"{artifact}: referee skips {test}: {answer.reason}", [path])
        return ruled(answer.upheld, answer.reason, self._model)

    @property
    def model(self) -> str:
        return self._model

    async def confirm(
        self, worktree: Worktree, claim: str, test_file: str, quotes: Sequence[str]
    ) -> tuple[bool, str] | None:
        """Whether the spec backs a red team's report, and why; None when no ruling was had.

        Plain code first: every quote must be the spec's own words, or the report is not confirmed.
        """
        passage = self.passages(worktree, quotes)
        if passage is None:
            return False, "a quote is not in the spec, word for word"
        request = ReportRequest(
            claim=claim, test_file=test_file[: self.FILE_LIMIT], quotes=tuple(quotes), passage=passage
        )
        answers: list[ReportAnswer] = []
        for _ in range(self.VOTES):
            try:
                answers.append(await self._worker.run(self.CONFIRM, request, ReportAnswer))
            except WorkerError:
                continue
            if self.majority([a.confirmed for a in answers]) is not None:
                break
        verdict = self.majority([a.confirmed for a in answers])
        if verdict is None:
            return None
        return verdict, next(a.reason for a in answers if a.confirmed == verdict)

    async def reconsider(
        self, worktree: Worktree, claim: str, test_file: str, quotes: Sequence[str], broken: Sequence[str]
    ) -> tuple[bool, str] | None:
        """Whether a confirmed report is withdrawn, given the protected tests its fix broke, and why; None
        when no ruling was had. `worktree` holds the broken tests."""
        passage = self.passages(worktree, quotes)
        if passage is None:
            return True, "a quote is not in the spec, word for word"
        conflicts = tuple(
            ConflictingTest(test=test, excerpt=self.excerpt(worktree, test))
            for test in broken[: self.MAX_CONFLICTS]
        )
        request = ReconsiderRequest(
            claim=claim,
            test_file=test_file[: self.FILE_LIMIT],
            quotes=tuple(quotes),
            passage=passage,
            conflicts=conflicts,
        )
        answers: list[ReconsiderAnswer] = []
        for _ in range(self.VOTES):
            try:
                answers.append(await self._worker.run(self.RECONSIDER, request, ReconsiderAnswer))
            except WorkerError:
                continue
            if self.majority([a.withdrawn for a in answers]) is not None:
                break
        verdict = self.majority([a.withdrawn for a in answers])
        if verdict is None:
            return None
        return verdict, next(a.reason for a in answers if a.withdrawn == verdict)

    @classmethod
    def excerpt(cls, worktree: Worktree, test: str) -> str:
        """A test's source around its case: the lines near its parameter id, else its function."""
        path, _, rest = test.partition("::")
        function, _, case = rest.partition("[")
        try:
            source = worktree.read(path)
        except (OSError, UnicodeDecodeError, ValueError):
            return "(the test file can't be read)"
        lines = source.split("\n")
        case = case.removesuffix("]")
        at = next((n for n, line in enumerate(lines) if case and case in line), None)
        if at is None:
            node = cls.definition(source, function)
            text = (ast.get_source_segment(source, node) or source) if node is not None else source
        else:
            text = "\n".join(lines[max(0, at - cls.EXCERPT_LINES) : at + cls.EXCERPT_LINES + 1])
        return text[: cls.EXCERPT_LIMIT]

    @classmethod
    def majority(cls, verdicts: Sequence[bool]) -> bool | None:
        """The majority verdict once one side has it; None while undecided."""
        needed = cls.VOTES // 2 + 1
        for verdict in (True, False):
            if sum(v == verdict for v in verdicts) >= needed:
                return verdict
        return None

    @classmethod
    def decided(cls, answers: Sequence[RulingAnswer]) -> bool | None:
        """The majority verdict on a dispute once one side has it; None while undecided."""
        return cls.majority([a.upheld for a in answers])

    def source(self, worktree: Worktree, path: str) -> str | None:
        if not path.startswith(f"{self.FOLDER}/") or not path.endswith(".py"):
            return None
        try:
            return worktree.read(path)
        except (OSError, UnicodeDecodeError, ValueError):  # missing, binary, or outside the project
            return None

    @staticmethod
    def definition(source: str, function: str) -> ast.FunctionDef | None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function), None)

    def passages(self, worktree: Worktree, quotes: Sequence[str]) -> str | None:
        """The spec around each quote, one after the other, in about the context one quote gets; None when
        there is no quote or one isn't in the spec word for word."""
        context = self.CONTEXT // max(1, len(quotes))
        found = [self.passage(worktree, quote, context) for quote in quotes]
        if not found or any(p is None for p in found):
            return None
        return "\n[...]\n".join(p for p in found if p is not None)

    def passage(self, worktree: Worktree, quote: str, context: int | None = None) -> str | None:
        """The spec around the quote, whitespace collapsed; None when no spec file holds it word for word."""
        context = self.CONTEXT if context is None else context
        wanted = " ".join(quote.split())
        if not wanted:
            return None
        for path in self._spec:
            try:
                text = " ".join(worktree.read(path).split())
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            at = text.find(wanted)
            if at >= 0:
                return text[max(0, at - context) : at + len(wanted) + context]
        return None

    @classmethod
    def skipped(cls, source: str, function: str, reason: str) -> str:
        """The test file with the function marked as skipped, the referee's reason given."""
        node = cls.definition(source, function)
        assert node is not None
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
        lines = source.split("\n")
        mark = f"@pytest.mark.skip(reason={('ruled wrong by the referee: ' + reason)!r})"
        lines.insert(first, mark)
        text = "\n".join(lines)
        has_pytest = any(
            isinstance(n, ast.Import) and any(a.name == "pytest" for a in n.names)
            for n in ast.parse(source).body
        )
        return text if has_pytest else "import pytest\n" + text
