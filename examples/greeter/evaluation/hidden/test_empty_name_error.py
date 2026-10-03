import pytest
from greeter import hello


def test_empty_names_are_rejected():
    for name in ["", "   "]:
        with pytest.raises(ValueError):
            hello(name)


def test_other_names_still_work():
    assert hello("Ada") == "Hello, Ada!"
