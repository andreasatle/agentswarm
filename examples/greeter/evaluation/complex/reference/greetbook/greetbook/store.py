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
        for entry in data["entries"]:
            book.add(entry["name"], entry["greeting"])
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"not a greeting book: {error}") from error
    return book
