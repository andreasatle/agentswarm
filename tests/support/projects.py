"""Opens small projects for end-to-end tests: a fake executor, local sandbox, and a scripted judge."""

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from agentswarm.core.clock import FakeClock
from agentswarm.core.config import ProjectConfig
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.decisions import InMemoryDecisions
from agentswarm.judging.judges import ChangeReadiness
from agentswarm.project import Project, ProjectServices
from tests.support.samples import Samples


class Projects:
    """Opens a project in a temporary directory with a fake executor and a scripted decider."""

    CONFIG: ClassVar[dict[str, Any]] = {
        "project": "demo",
        "domain": "software",
        "north_star": "A small demo project",
        "levels": ["raw", "experimental"],
        "sandbox": {"kind": "local"},
        "checks": {
            # Every markdown file in the tree must be non-empty.
            "non_empty": {
                "command": [
                    sys.executable,
                    "-c",
                    "import pathlib, sys; "
                    "sys.exit(any(p.stat().st_size == 0 for p in pathlib.Path().glob('*.md')))",
                ],
                "kind": "test_failure",
            }
        },
        "teams": [{"use": "fake_executor", "watches": "raw"}],
        "gates": [{"from": "raw", "to": "experimental", "hard_checks": ["non_empty"]}],
    }

    @classmethod
    def open(
        cls, root: Path, judged: Sequence[ChangeReadiness] | None = None, persistent_decisions: bool = False
    ) -> Project:
        """Without `judged`, the gate has hard checks only; with it, an active judge answering in turn."""
        config = cls.CONFIG
        if judged is not None:
            gate = config["gates"][0] | {"judge": "ChangeReadiness"}
            config = config | {"gates": [gate], "jev": {"mode": "active"}}
        return cls.open_config(ProjectConfig.model_validate(config), root, judged, persistent_decisions)

    @staticmethod
    def open_config(
        config: ProjectConfig,
        root: Path,
        judged: Sequence[ChangeReadiness] | None = None,
        persistent_decisions: bool = False,
    ) -> Project:
        """By default decisions stay in memory; `persistent_decisions` uses the real decisions.sqlite."""
        decisions = None if persistent_decisions else InMemoryDecisions()
        services = ProjectServices(
            decider=FakeDecider(judged or ()), decisions=decisions, clock=FakeClock(Samples.AT)
        )
        return Project.open(config, root, services)
