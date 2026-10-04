"""Reading visits from CSV."""

import csv
import datetime
from pathlib import Path


class InputError(Exception):
    """The input can't be read as visits."""


def read(path: str) -> tuple[list[tuple[str, datetime.date]], int]:
    """(valid visits, number of skipped rows)."""
    file = Path(path)
    if not file.is_file():
        raise InputError(f"no such file: {path}")
    with file.open(newline="") as handle:
        rows = csv.DictReader(handle)
        if rows.fieldnames is None or not {"name", "date"} <= set(rows.fieldnames):
            raise InputError(f"{path} needs the columns name and date")
        visits: list[tuple[str, datetime.date]] = []
        skipped = 0
        for row in rows:
            name = (row.get("name") or "").strip()
            try:
                day = datetime.date.fromisoformat((row.get("date") or "").strip())
            except ValueError:
                day = None
            if not name or day is None:
                skipped += 1
                continue
            visits.append((name, day))
    return visits, skipped
