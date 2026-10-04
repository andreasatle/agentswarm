"""The Markdown report, and the command line."""

import argparse
import sys
from pathlib import Path

from visits.reading import InputError, read
from visits.stats import Visitor, per_person


def render(visitors: list[Visitor], minimum: int, skipped: int) -> str:
    lines = ["# Visits", "", "| Name | Visits | Last visit |", "| --- | --- | --- |"]
    lines += [f"| {v.name} | {v.count} | {v.last.isoformat()} |" for v in visitors]
    lines += ["", "## Regulars", ""]
    regulars = [v for v in visitors if v.count >= minimum]
    lines += [f"- Hello, {v.name}!" for v in regulars] or ["No regulars."]
    if skipped:
        lines += ["", f"Skipped {skipped} invalid rows."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    top = argparse.ArgumentParser(prog="visits")
    top.add_argument("input")
    top.add_argument("output")
    top.add_argument("--min", type=int, default=3)
    args = top.parse_args(argv)
    try:
        visits, skipped = read(args.input)
    except InputError as error:
        print(error, file=sys.stderr)
        return 1
    Path(args.output).write_text(render(per_person(visits), args.min, skipped))
    return 0
