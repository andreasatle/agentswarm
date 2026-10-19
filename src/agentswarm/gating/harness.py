"""The evaluator's own pytest setup for the tests that judge a producer's work (an outside audit, 2026-10-19,
A02: a producer's root conftest.py replaced every test with one that passes; the acceptance files were
restored untouched, and the ratchet counted 3 of 3).

Before a ratchet or a score counts tests, plain code makes a throwaway copy of the tree and takes the
producer's levers on pytest out of it: every conftest.py the producer added or changed outside the protected
test paths (hooks there run for every test collected), files at the root that would stand in for pytest or its
plugin machinery when Python imports them from the working directory, and the producer's pytest configuration,
replaced by the evaluator's own (no added options, so no plugin or flag slipped in through `addopts`).

What this does not close: the product's own code runs in the same process as the tests, so a module the
tests import could still patch pytest from inside. That needs the tests run apart from the product, with
inputs and outputs passed across (docs/BACKLOG.md).
"""

import shutil
import uuid
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar


class TestHarness:
    __test__ = False  # not a pytest test class, despite its name

    # Names a module at the root could take to be imported instead of pytest's own (the working directory
    # comes first on sys.path under `python -m pytest`)
    SHADOWING: ClassVar[tuple[str, ...]] = (
        "pytest",
        "_pytest",
        "pluggy",
        "iniconfig",
        "py",
        "sitecustomize",
        "usercustomize",
    )
    CONFIG = "pytest.ini"  # takes precedence over pyproject.toml, tox.ini and setup.cfg in the same directory
    INI = "[pytest]\naddopts =\n"
    IGNORED: ClassVar[tuple[str, ...]] = (".git", "__pycache__")

    def __init__(self, protected: Sequence[str], trusted: Mapping[str, str] | None = None) -> None:
        self._protected = tuple(protected)  # path prefixes of the tests the evaluator owns
        # conftest.py files as they were before the producer's work (by path): kept when unchanged. None:
        # nothing to compare with (the tree is the level itself, say), so only the protected paths decide
        self._trusted = trusted

    @contextmanager
    def copy(self, source: Path) -> Generator[Path]:
        """A guarded throwaway copy of `source`, beside it, for one test run."""
        path = source.parent / f".eval-{uuid.uuid4().hex[:12]}"
        shutil.copytree(source, path, symlinks=True, ignore=shutil.ignore_patterns(*self.IGNORED))
        try:
            self.guard(path)
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def guard(self, root: Path) -> list[str]:
        """Takes the producer's levers on pytest out of `root` (a copy, never a worktree); says what went."""
        removed: list[str] = []
        for conftest in sorted(root.rglob("conftest.py")):
            relative = conftest.relative_to(root).as_posix()
            if not self.kept(relative, conftest):
                conftest.unlink()
                removed.append(relative)
        for name in self.SHADOWING:
            for candidate in (root / f"{name}.py", root / name, root / f"{name}.pth"):
                if candidate.is_symlink() or candidate.is_file():
                    candidate.unlink()
                    removed.append(candidate.name)
                elif candidate.is_dir():
                    shutil.rmtree(candidate)
                    removed.append(f"{candidate.name}/")
        config = root / self.CONFIG
        if config.is_symlink() or config.exists():
            removed.append(self.CONFIG)
            if config.is_dir() and not config.is_symlink():
                shutil.rmtree(config)
            else:
                config.unlink()
        config.write_text(self.INI)
        return removed

    def kept(self, relative: str, conftest: Path) -> bool:
        """Under a protected path; or as it was before the producer's work; or, with nothing to compare with
        and nothing protected, the tree's own (there is no oracle to guard then)."""
        if any(relative.startswith(prefix) for prefix in self._protected):
            return True
        if self._trusted is not None:
            return self._trusted.get(relative) == conftest.read_text(errors="replace")
        return not self._protected
