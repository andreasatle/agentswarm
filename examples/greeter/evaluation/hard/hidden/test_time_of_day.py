import pytest
from greeter import timed_greeting


def test_boundaries():
    expected = {
        0: "night",
        4: "night",
        5: "morning",
        11: "morning",
        12: "afternoon",
        17: "afternoon",
        18: "evening",
        21: "evening",
        22: "night",
        23: "night",
    }
    for hour, part in expected.items():
        assert timed_greeting("Ada", hour) == f"Good {part}, Ada!"


def test_invalid_hours():
    for hour in [-1, 24]:
        with pytest.raises(ValueError):
            timed_greeting("Ada", hour)
