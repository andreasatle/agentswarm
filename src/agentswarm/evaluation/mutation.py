"""Mutation testing: ground truth for the judge's `tests_relevant` question (Phase 2 design, step 6).

The idea: break the code a change added, one small edit at a time, and rerun the tests the change added.
Tests that exercise the new behaviour notice ("kill" the mutant); tests that pass trivially don't.

    1. Diff the judged commit against the level it goes into; collect changed lines per file.
    2. Mutants: small edits on changed lines of changed non-test Python files (see `Mutator`).
    3. Run only the change's own test files, on the unmutated code first (they must pass), then once per
       mutant. A failing run kills the mutant.
    4. tests_relevant is true if the change added tests and they kill at least half of the mutants.

Python only, deliberately small and deterministic: enough to tell real tests from `assert True`.
"""

import ast
import copy
import re
from pathlib import PurePosixPath
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from agentswarm.workspace.git import Repository, Worktree
from agentswarm.workspace.sandbox import Sandbox


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Mutant(Frozen):
    path: str
    line: int
    description: str  # e.g. "'==' -> '!='"
    source: str  # the whole mutated file


class MutationReport(Frozen):
    test_files: tuple[str, ...]  # the change's own test files
    mutants: int
    killed: int

    @property
    def score(self) -> float:
        return self.killed / self.mutants if self.mutants else 0.0


class ChangedLines:
    """Changed (new-side) line numbers per file, from a zero-context unified diff."""

    HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    @classmethod
    def parse(cls, diff: str) -> dict[str, set[int]]:
        changed: dict[str, set[int]] = {}
        path: str | None = None
        for line in diff.splitlines():
            if line.startswith("+++ "):
                target = line[4:]
                path = target[2:] if target.startswith("b/") else None  # "+++ /dev/null": deleted file
                if path is not None:
                    changed.setdefault(path, set())
            elif (hunk := cls.HUNK.match(line)) and path is not None:
                start, count = int(hunk.group(1)), int(hunk.group(2) or "1")
                changed[path].update(range(start, start + count))
        return changed

    @staticmethod
    def is_test(path: str) -> bool:
        p = PurePosixPath(path)
        return p.suffix == ".py" and (
            "tests" in p.parts or p.name.startswith("test_") or p.stem.endswith("_test")
        )


class Mutator(ast.NodeTransformer):
    """Applies the `target`-th possible mutation on the given lines; counts the candidates as it goes."""

    COMPARISONS: ClassVar[dict[type[ast.cmpop], type[ast.cmpop]]] = {
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        ast.Lt: ast.GtE,
        ast.GtE: ast.Lt,
        ast.Gt: ast.LtE,
        ast.LtE: ast.Gt,
        ast.In: ast.NotIn,
        ast.NotIn: ast.In,
    }
    ARITHMETIC: ClassVar[dict[type[ast.operator], type[ast.operator]]] = {
        ast.Add: ast.Sub,
        ast.Sub: ast.Add,
        ast.Mult: ast.FloorDiv,
        ast.FloorDiv: ast.Mult,
    }

    def __init__(self, lines: set[int], target: int) -> None:
        self._lines = lines
        self._target = target
        self.candidates = 0
        self.applied: tuple[int, str] | None = None  # (line, description) of the mutation made

    def _take(self, node: ast.AST, description: str) -> bool:
        """Counts a candidate on a changed line; True if it is the one to apply."""
        line = getattr(node, "lineno", None)
        if line not in self._lines:
            return False
        index = self.candidates
        self.candidates += 1
        if index == self._target:
            self.applied = (line, description)
            return True
        return False

    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node  # a docstring: changing it changes nothing
        return self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        op = node.ops[0]
        swapped = self.COMPARISONS.get(type(op))
        if swapped is not None and self._take(node, f"{type(op).__name__} -> {swapped.__name__}"):
            node.ops[0] = swapped()
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        swapped = self.ARITHMETIC.get(type(node.op))
        if swapped is not None and self._take(node, f"{type(node.op).__name__} -> {swapped.__name__}"):
            node.op = swapped()
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bool):
            if self._take(node, f"{node.value} -> {not node.value}"):
                return ast.copy_location(ast.Constant(not node.value), node)
        elif isinstance(node.value, str) and node.value:
            if self._take(node, f"string {node.value[:20]!r} changed"):
                return ast.copy_location(ast.Constant("~" + node.value), node)
        elif isinstance(node.value, int) and self._take(node, f"{node.value} -> {node.value + 1}"):
            return ast.copy_location(ast.Constant(node.value + 1), node)
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        if self._take(node, "if condition negated"):
            node.test = (
                node.test.operand
                if isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)
                else ast.UnaryOp(ast.Not(), node.test)
            )
        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:
        self.generic_visit(node)
        if node.value is not None and self._take(node, "return value -> None"):
            node.value = ast.Constant(None)
        return node


class MutantGenerator:
    MAX_PER_FILE = 12  # keeps runs short; the score needs a handful of mutants, not all of them

    def mutants(self, path: str, source: str, lines: set[int]) -> list[Mutant]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        mutants: list[Mutant] = []
        target = 0
        while len(mutants) < self.MAX_PER_FILE:
            mutator = Mutator(lines, target)
            mutated = mutator.visit(copy.deepcopy(tree))
            if mutator.applied is None:
                break  # no candidate left
            line, description = mutator.applied
            mutants.append(Mutant(path=path, line=line, description=description, source=ast.unparse(mutated)))
            target += 1
        return mutants


class MutationTesting:
    """Runs mutation testing for one judged commit, in a throwaway checkout, in the sandbox."""

    MIN_SCORE = 0.5  # tests_relevant is true from this kill ratio on

    def __init__(self, command: tuple[str, ...], timeout_s: float, sandbox: Sandbox) -> None:
        self._command = command
        self._timeout_s = timeout_s
        self._sandbox = sandbox
        self._generator = MutantGenerator()

    def run(self, repository: Repository, commit: str, base: str, name: str) -> MutationReport | None:
        """Mutation-test the change from `base` to `commit`.

        None when there is no answer: no mutable changed code, or the change's tests fail unmutated.
        """
        worktree = repository.checkout_detached(commit, f"_mutation-{name}")
        try:
            return self._run(worktree, base)
        finally:
            worktree.remove()

    def truth(self, report: MutationReport) -> bool:
        return bool(report.test_files) and report.score >= self.MIN_SCORE

    def _run(self, worktree: Worktree, base: str) -> MutationReport | None:
        changed = ChangedLines.parse(worktree.diff_against(base, context_lines=0))
        existing = {path: lines for path, lines in changed.items() if (worktree.path / path).is_file()}
        tests = tuple(sorted(p for p in existing if ChangedLines.is_test(p)))
        if not tests:
            return MutationReport(test_files=(), mutants=0, killed=0)  # no new tests: not relevant
        mutants = [
            mutant
            for path, lines in sorted(existing.items())
            if path.endswith(".py") and not ChangedLines.is_test(path)
            for mutant in self._generator.mutants(path, worktree.read(path), lines)
        ]
        if not mutants or not self._passes(worktree, tests):
            return None
        killed = 0
        for mutant in mutants:
            original = worktree.read(mutant.path)
            worktree.write(mutant.path, mutant.source)
            try:
                killed += not self._passes(worktree, tests)
            finally:
                worktree.write(mutant.path, original)
        return MutationReport(test_files=tests, mutants=len(mutants), killed=killed)

    def _passes(self, worktree: Worktree, tests: tuple[str, ...]) -> bool:
        return self._sandbox.run((*self._command, *tests), worktree.path, self._timeout_s).passed
