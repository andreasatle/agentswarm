"""The JSON file a book lives in."""

import json
from pathlib import Path

from greetbook.book import BookError, GreetingBook


def load(path: str) -> GreetingBook:
    file = Path(path)
    if not file.exists():
        return GreetingBook()
    try:
        data = json.loads(file.read_text())
        return GreetingBook({e["name"]: e["greeting"] for e in data["entries"]})
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise BookError(f"{path} is not a greeting book: {error}") from error


def save(book: GreetingBook, path: str) -> None:
    entries = [{"name": name, "greeting": greeting} for name, greeting in book.items()]
    Path(path).write_text(json.dumps({"entries": entries}, indent=2) + "\n")
