import pytest
from greeter import greet_weekday


def test_days():
    assert greet_weekday("Ada", 0) == "Happy Monday, Ada!"
    assert greet_weekday("Ada", 4) == "Happy Friday, Ada!"
    assert greet_weekday("Ada", 6) == "Happy Sunday, Ada!"


@pytest.mark.parametrize("day", [-1, 7])
def test_bad_day(day):
    with pytest.raises(ValueError):
        greet_weekday("Ada", day)
