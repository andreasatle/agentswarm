"""Host-side access to the files of one directory, usually a worktree (CODEX_AUDIT.md F02).

Every read and write of project files on the host, by model tools and by framework code alike, goes
through `ConfinedFiles`: paths are relative and stay inside the root. Absolute paths, `..`, Git metadata
and symlinks that lead outside are refused, and reads are bounded before the content is loaded.

Checking the resolved path and then opening it is enough here because nothing untrusted writes the
worktree concurrently: sandboxed commands run on a throwaway copy (see sandbox.py, `RunCopy`).
"""

from pathlib import Path, PurePosixPath


class UnsafePath(ValueError):
    """A path that is absolute, climbs out, names Git metadata or leaves the root through a link."""


class ConfinedFiles:
    GIT = ".git"

    def __init__(self, root: Path) -> None:
        self._root = root

    def relative(self, path: str) -> str:
        """`path` normalised relative to the root; raises UnsafePath for anything that could escape it."""
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise UnsafePath(f"path must be relative and stay inside the project: {path!r}")
        if not candidate.parts or self.GIT in candidate.parts:
            raise UnsafePath(f"not a project file path: {path!r}")
        if not (self._root / candidate).resolve().is_relative_to(self._root.resolve()):
            raise UnsafePath(f"path leaves the project through a link: {path!r}")
        return str(candidate)

    def path(self, path: str) -> Path:
        return self._root / self.relative(path)

    def read(self, path: str, limit: int | None = None) -> str:
        """The file's text, at most `limit` characters of it; only that much is read."""
        with self.path(path).open(encoding="utf-8") as file:
            return file.read(-1 if limit is None else limit)

    def write(self, path: str, content: str) -> None:
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def delete(self, path: str) -> None:
        self.path(path).unlink()
