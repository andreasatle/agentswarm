"""A reminder: greet someone on a day of the week."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reminder:
    name: str
    day: int  # 0 = Monday ... 6 = Sunday
    message: str

    def __post_init__(self) -> None:
        if not 0 <= self.day <= 6:
            raise ValueError(f"day must be 0-6, not {self.day}")
        if not self.name.strip() or not self.message.strip():
            raise ValueError("name and message must not be empty")
