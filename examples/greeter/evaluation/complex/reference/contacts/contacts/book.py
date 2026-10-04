"""The address book."""

from greeter import hello

from contacts.model import Contact


class AddressBook:
    def __init__(self) -> None:
        self._contacts: dict[str, Contact] = {}  # lower-case email -> contact

    def add(self, contact: Contact) -> None:
        key = contact.email.lower()
        if key in self._contacts:
            raise ValueError(f"{contact.email} is already in the book")
        self._contacts[key] = contact

    def find(self, name: str) -> list[Contact]:
        found = [c for c in self._contacts.values() if c.name.lower() == name.lower()]
        return sorted(found, key=lambda c: c.email.lower())

    def by_domain(self, domain: str) -> list[Contact]:
        found = [c for c in self._contacts.values() if c.domain.lower() == domain.lower()]
        return sorted(found, key=lambda c: c.name.lower())

    def greet_domain(self, domain: str) -> list[str]:
        return [hello(c.name) for c in self.by_domain(domain)]
