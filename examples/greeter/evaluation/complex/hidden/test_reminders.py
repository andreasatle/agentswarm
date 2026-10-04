import dataclasses

import pytest
from reminders import Reminder, Schedule


def test_validation():
    with pytest.raises(ValueError):
        Reminder("Ada", 7, "Hi")
    with pytest.raises(ValueError):
        Reminder(" ", 0, "Hi")
    with pytest.raises(ValueError):
        Reminder("Ada", 0, " ")
    with pytest.raises(dataclasses.FrozenInstanceError):
        Reminder("Ada", 0, "Hi").day = 1


def test_schedule():
    s = Schedule()
    s.add(Reminder("Bob", 0, "Yo"))
    s.add(Reminder("Ada", 0, "Hi"))
    s.add(Reminder("Ada", 4, "Happy Friday"))
    assert [r.name for r in s.for_day(0)] == ["Ada", "Bob"]
    with pytest.raises(ValueError):
        s.add(Reminder("Ada", 0, "again"))
    s.remove("Bob", 0)
    assert [r.name for r in s.for_day(0)] == ["Ada"]
    with pytest.raises(KeyError):
        s.remove("Bob", 0)


def test_text_round_trip():
    s = Schedule()
    s.add(Reminder("Bob", 4, "Yo"))
    s.add(Reminder("Ada", 4, "Hi"))
    s.add(Reminder("Cy", 0, "Hey"))
    text = s.to_text()
    assert text == "Mon|Cy|Hey\nFri|Ada|Hi\nFri|Bob|Yo\n"
    assert Schedule.from_text("\n" + text + "\n").to_text() == text


@pytest.mark.parametrize(
    "text, line", [("Mon|Ada|Hi\nFunday|Bob|Yo", 2), ("Mon|Ada", 1), ("\nMon|Ada|Hi\nMon|Ada|Hi", 3)]
)
def test_bad_lines(text, line):
    with pytest.raises(ValueError, match=f"line {line}"):
        Schedule.from_text(text)
