"""Saving and loading a greeting book as JSON."""

import json
from pathlib import Path

from greetbook.book import GreetingBook


def save(book: GreetingBook, path: str | Path) -> None:
    entries = [{"name": name, "greeting": book.get(name)} for name in book.names()]
    Path(path).write_text(json.dumps({"entries": entries}, indent=2) + "\n")


def load(path: str | Path) -> GreetingBook:
    book = GreetingBook()
    file = Path(path)
    if not file.exists():
        return book
    try:
        data = json.loads(file.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"not a greeting book: {error}") from error
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("not a greeting book: 'entries' must be a list")
    for entry in entries:
        valid = isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("name", "greeting"))
        if not valid:
            raise ValueError(f"not a greeting book entry: {entry!r}")
        book.add(entry["name"], entry["greeting"])  # ValueError for an empty or duplicate name
    return book
