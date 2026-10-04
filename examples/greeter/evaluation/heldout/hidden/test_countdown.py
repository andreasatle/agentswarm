import pytest
from greeter import countdown_hello


def test_countdown():
    assert countdown_hello("Ada", 2) == "2...\n1...\nHello, Ada!"
    assert countdown_hello("Ada", 0) == "Hello, Ada!"


def test_negative():
    with pytest.raises(ValueError):
        countdown_hello("Ada", -1)
