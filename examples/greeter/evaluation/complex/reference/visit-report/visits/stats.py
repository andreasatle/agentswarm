"""Counting visits per person."""

import datetime
from dataclasses import dataclass


@dataclass
class Visitor:
    name: str
    count: int
    last: datetime.date


def per_person(visits: list[tuple[str, datetime.date]]) -> list[Visitor]:
    people: dict[str, Visitor] = {}
    for name, day in visits:
        key = name.lower()
        if key not in people:
            people[key] = Visitor(name, 0, day)
        person = people[key]
        person.count += 1
        person.last = max(person.last, day)
    return sorted(people.values(), key=lambda v: (-v.count, v.name.lower()))
