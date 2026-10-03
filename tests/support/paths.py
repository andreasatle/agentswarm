"""Paths into the repository, so tests don't depend on how deep their own file sits."""

from pathlib import Path


class RepoPaths:
    ROOT = Path(__file__).resolve().parents[2]
    EXAMPLES = ROOT / "examples"
    GREETER = EXAMPLES / "greeter"
