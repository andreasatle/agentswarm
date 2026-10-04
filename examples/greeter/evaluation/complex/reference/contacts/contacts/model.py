"""A contact: a name and an email address."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Contact:
    name: str
    email: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")
        local, at, domain = self.email.partition("@")
        if not at or "@" in domain or not local or "." not in domain or " " in self.email:
            raise ValueError(f"invalid email: {self.email!r}")

    @property
    def domain(self) -> str:
        return self.email.partition("@")[2]
