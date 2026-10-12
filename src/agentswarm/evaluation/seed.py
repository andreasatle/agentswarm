"""Seed projects from a test suite with known answers: what every real problem's importer shares.

A real problem (CommonMark, TOML, jq, YAML) comes with its own tests. Its importer reads them as cases, puts
them in groups (one test file each, which the worklist turns into a work item each), and writes a seed
project: a stub that fails nearly everything, the visible tests under tests/acceptance/ where no producer can
change them, and a held-out set, every fifth case of each group, that no agent ever sees and only scoring
runs. What differs per problem is how its tests are read, the stub, the helper files and docs, and how a
group's cases become a test file; that is all a subclass supplies.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class SeedGroup[C](BaseModel):
    """The cases of one test file, in the order they are tested."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # fits a Python module name, e.g. "anchor" or "rejected_2"
    title: str  # what the test file's docstring calls it
    cases: tuple[C, ...]


class SeedSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: int
    cases: int
    visible: int  # tests, which can be more than cases: a YAML case has a test for its events and its values
    held_out: int
    seed: Path
    held_out_tests: Path

    def render(self, unit: str = "tests") -> str:
        return (
            f"{self.groups} groups of {self.cases} cases: {self.visible} {unit} in {self.seed}, "
            f"{self.held_out} held out in {self.held_out_tests}"
        )


class SeedImport[C](ABC):
    HELD_OUT_EVERY: ClassVar[int] = 5  # within each group, the fifth, tenth, ... case is held out
    PREFIX: ClassVar[str]  # of the visible test files, e.g. "test_jq"

    def __init__(self, out: Path) -> None:
        self._out = out

    @abstractmethod
    def groups(self) -> list[SeedGroup[C]]:
        """Every case, grouped, in the order the work should take them."""

    @abstractmethod
    def files(self) -> Mapping[str, str]:
        """The seed's other files, by path in the seed: the stub, test helpers, docs for the agents."""

    @abstractmethod
    def test(self, title: str, cases: Sequence[C]) -> str:
        """A test file for some of a group's cases."""

    def held(self, position: int, case: C) -> bool:
        """Whether the case at this place in its group (from 1) is held out."""
        return position % self.HELD_OUT_EVERY == 0

    def count(self, cases: Sequence[C]) -> int:
        """How many tests these cases make."""
        return len(cases)

    def extra(self) -> Mapping[str, str]:
        """Files beside the held-out tests, by name (a conftest the visible tests have too, say)."""
        return {}

    def write(self) -> SeedSummary:
        groups = self.groups()
        seed = self._out / "seed"
        hidden = self._out / "held_out"
        acceptance = seed / "tests" / "acceptance"
        acceptance.mkdir(parents=True, exist_ok=True)
        hidden.mkdir(parents=True, exist_ok=True)
        for path, text in self.files().items():
            (seed / path).parent.mkdir(parents=True, exist_ok=True)
            (seed / path).write_text(text)
        for name, text in self.extra().items():
            (hidden / name).write_text(text)
        visible = held = 0
        for index, group in enumerate(groups, 1):
            shown = [c for n, c in enumerate(group.cases, 1) if not self.held(n, c)]
            kept = [c for n, c in enumerate(group.cases, 1) if self.held(n, c)]
            name = f"{index:02d}_{group.name}"
            if shown:
                (acceptance / f"{self.PREFIX}_{name}.py").write_text(self.test(group.title, shown))
            if kept:
                (hidden / f"test_held_out_{name}.py").write_text(self.test(group.title, kept))
            visible, held = visible + self.count(shown), held + self.count(kept)
        return SeedSummary(
            groups=len(groups),
            cases=sum(len(g.cases) for g in groups),
            visible=visible,
            held_out=held,
            seed=seed,
            held_out_tests=hidden,
        )

    @staticmethod
    def slug(title: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")

    @classmethod
    def cut(cls, title: str, units: Sequence[Sequence[C]], most: int) -> list[SeedGroup[C]]:
        """Groups of at most `most` cases, never splitting a unit (a manual entry's examples, say)."""
        parts: list[list[C]] = []
        for unit in units:
            if not parts or (parts[-1] and len(parts[-1]) + len(unit) > most):
                parts.append([])
            parts[-1] += unit
        slug = cls.slug(title)
        if len(parts) <= 1:
            return [SeedGroup(name=slug, title=slug, cases=tuple(p)) for p in parts]
        return [
            SeedGroup(name=f"{slug}_{n}", title=f"{slug}_{n}", cases=tuple(p)) for n, p in enumerate(parts, 1)
        ]
