import pytest
from greeter import greet_nth


def test_suffixes():
    expected = {
        1: "1st",
        2: "2nd",
        3: "3rd",
        4: "4th",
        11: "11th",
        12: "12th",
        13: "13th",
        21: "21st",
        22: "22nd",
        23: "23rd",
        101: "101st",
        111: "111th",
        112: "112th",
    }
    for n, ordinal in expected.items():
        assert greet_nth("Ada", n) == f"Hello, Ada, you are {ordinal}!"


def test_invalid():
    for n in [0, -3]:
        with pytest.raises(ValueError):
            greet_nth("Ada", n)
