"""A week of reminders, and its text format."""

from typing import Self

from reminders.model import Reminder

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class Schedule:
    def __init__(self) -> None:
        self._reminders: dict[tuple[int, str], Reminder] = {}

    def add(self, reminder: Reminder) -> None:
        key = (reminder.day, reminder.name)
        if key in self._reminders:
            raise ValueError(f"{reminder.name} already has a reminder on {DAYS[reminder.day]}")
        self._reminders[key] = reminder

    def remove(self, name: str, day: int) -> None:
        del self._reminders[(day, name)]

    def for_day(self, day: int) -> list[Reminder]:
        return [r for (d, _), r in sorted(self._reminders.items()) if d == day]

    def to_text(self) -> str:
        return "".join(f"{DAYS[r.day]}|{r.name}|{r.message}\n" for _, r in sorted(self._reminders.items()))

    @classmethod
    def from_text(cls, text: str) -> Self:
        schedule = cls()
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            parts = line.split("|")
            try:
                if len(parts) != 3 or parts[0] not in DAYS:
                    raise ValueError("expected 'Day|name|message'")
                schedule.add(Reminder(parts[1], DAYS.index(parts[0]), parts[2]))
            except ValueError as error:
                raise ValueError(f"line {number}: {error}") from error
        return schedule
