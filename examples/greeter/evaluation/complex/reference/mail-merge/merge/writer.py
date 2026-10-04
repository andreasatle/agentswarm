"""Writing one file per contact, and the command line."""

import argparse
import re
import sys
from pathlib import Path

from merge import contacts, template


class MergeError(Exception):
    """The inputs can't be merged."""


def slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def plan(template_path: str, contacts_path: str) -> list[tuple[str, str]]:
    """(slug, text) for every file to write; raises MergeError before anything is written."""
    try:
        text = Path(template_path).read_text()
        columns, rows = contacts.read(contacts_path)
    except OSError as error:
        raise MergeError(str(error)) from error
    if "name" not in columns:
        raise MergeError("the contacts need a 'name' column")
    unknown = template.fields(text) - set(columns)
    if unknown:
        raise MergeError(f"the template uses fields that aren't columns: {sorted(unknown)}")
    taken: set[str] = set()
    return [
        (slug(row["name"], taken), template.fill(text, row)) for row in rows if (row["name"] or "").strip()
    ]


def main(argv: list[str]) -> int:
    top = argparse.ArgumentParser(prog="merge")
    top.add_argument("template")
    top.add_argument("contacts")
    top.add_argument("outdir")
    args = top.parse_args(argv)
    try:
        files = plan(args.template, args.contacts)
    except MergeError as error:
        print(error, file=sys.stderr)
        return 1
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for name, text in files:
        (outdir / f"{name}.txt").write_text(text)
    print(f"Wrote {len(files)} files.")
    return 0
