"""A compact map of a worktree's code, given to builders up front (after Aider's "repo map").

Builders on a parser of a thousand lines spent their whole step budget reading files to find their way
(CommonMark, 2026-10-09: 34-67 reads and no edit in 60 calls). The map lists each file with its size, and
for Python files the top-level functions and classes (with their methods) and the lines they span, so a
builder can read just the part it needs. Plain code (Python's own parser); a file that doesn't parse is
listed without symbols.
"""

import ast
from collections.abc import Sequence

from agentswarm.workspace.git import Worktree


class RepoMap:
    LIMIT = 8_000  # characters: a map, not a copy of the code
    SKIP = ("tests/acceptance/",)  # their content is the task; the builder runs them, it needn't map them

    def __init__(self, worktree: Worktree) -> None:
        self._worktree = worktree

    def render(self, files: Sequence[str] | None = None) -> str:
        lines: list[str] = []
        for path in files if files is not None else self._worktree.files():
            try:
                text = self._worktree.read(path)
            except (OSError, UnicodeDecodeError):
                continue
            count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            lines.append(f"{path} ({count} lines)")
            if path.endswith(".py") and not path.startswith(self.SKIP):
                lines += self.symbols(text)
        rendered = "\n".join(lines)
        if len(rendered) > self.LIMIT:
            return rendered[: self.LIMIT] + "\n... (map truncated)"
        return rendered

    @staticmethod
    def symbols(source: str) -> list[str]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return ["  (doesn't parse)"]
        found: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append(f"  def {node.name}  lines {node.lineno}-{node.end_lineno}")
            elif isinstance(node, ast.ClassDef):
                found.append(f"  class {node.name}  lines {node.lineno}-{node.end_lineno}")
                found += [
                    f"    def {item.name}  lines {item.lineno}-{item.end_lineno}"
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
        return found
