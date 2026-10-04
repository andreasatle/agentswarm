"""Reading contacts from CSV."""

import csv
from pathlib import Path


def read(path: str) -> tuple[list[str], list[dict[str, str]]]:
    with Path(path).open(newline="") as handle:
        rows = csv.DictReader(handle)
        columns = list(rows.fieldnames or [])
        return columns, [dict(row) for row in rows]
