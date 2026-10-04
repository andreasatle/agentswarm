import pytest
from greeter import ascii_hello


def test_strips():
    assert ascii_hello("Zoë") == "Hello, Zo!"
    assert ascii_hello("Ada") == "Hello, Ada!"


def test_nothing_left():
    with pytest.raises(ValueError):
        ascii_hello("ëï")
