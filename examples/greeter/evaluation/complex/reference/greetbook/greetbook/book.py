"""The greeting book itself."""


class DuplicateName(ValueError):
    """A name that is already in the book."""


class GreetingBook:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str]] = {}  # lower-case name -> (name, greeting)

    def add(self, name: str, greeting: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        key = name.lower()
        if key in self._entries:
            raise DuplicateName(name)
        self._entries[key] = (name, greeting)

    def get(self, name: str) -> str:
        return self._entries[name.strip().lower()][1]

    def remove(self, name: str) -> None:
        del self._entries[name.strip().lower()]

    def names(self) -> list[str]:
        return [self._entries[key][0] for key in sorted(self._entries)]

    def search(self, prefix: str) -> list[str]:
        start = prefix.lower()
        return [name for name in self.names() if name.lower().startswith(start)]

    def __len__(self) -> int:
        return len(self._entries)
