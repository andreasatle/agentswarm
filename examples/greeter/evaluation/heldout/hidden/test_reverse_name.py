import pytest
from greeter import greet_backwards


def test_reversed():
    assert greet_backwards("Ada") == "Hello, adA!"
    assert greet_backwards("Bob Lee") == "Hello, eeL boB!"


def test_empty():
    with pytest.raises(ValueError):
        greet_backwards("")
