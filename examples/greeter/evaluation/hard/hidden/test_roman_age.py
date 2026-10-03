import pytest
from greeter import greet_with_age


def test_roman_ages():
    assert greet_with_age("Ada", 42) == "Hello, Ada! You are XLII."
    assert greet_with_age("Ada", 1994) == "Hello, Ada! You are MCMXCIV."
    assert greet_with_age("Ada", 3999) == "Hello, Ada! You are MMMCMXCIX."
    assert greet_with_age("Ada", 4) == "Hello, Ada! You are IV."


def test_out_of_range():
    for age in [0, -1, 4000, 2.5]:
        with pytest.raises(ValueError):
            greet_with_age("Ada", age)
