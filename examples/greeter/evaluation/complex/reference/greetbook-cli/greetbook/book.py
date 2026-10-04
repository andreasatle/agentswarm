"""The greeting book."""


class BookError(Exception):
    """Something the user asked for that the book can't do."""


class GreetingBook:
    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self._entries: dict[str, tuple[str, str]] = {}
        for name, greeting in (entries or {}).items():
            self.add(name, greeting)

    def add(self, name: str, greeting: str) -> None:
        if name.lower() in self._entries:
            raise BookError(f"{name} is already in the book")
        self._entries[name.lower()] = (name, greeting)

    def get(self, name: str) -> str:
        if name.lower() not in self._entries:
            raise BookError(f"no entry for {name}")
        return self._entries[name.lower()][1]

    def remove(self, name: str) -> None:
        if name.lower() not in self._entries:
            raise BookError(f"no entry for {name}")
        del self._entries[name.lower()]

    def items(self) -> list[tuple[str, str]]:
        return [self._entries[key] for key in sorted(self._entries)]

    def search(self, prefix: str) -> list[str]:
        return [name for name, _ in self.items() if name.lower().startswith(prefix.lower())]
