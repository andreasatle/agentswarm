import pytest
from greeter import short_hello


def test_fits():
    assert short_hello("Ada", 12) == "Hello, Ada!"


def test_shortened():
    assert short_hello("Alexandra", 15) == "Hello, Alex...!"
    assert len(short_hello("Bartholomew", 13)) == 13
    assert short_hello("Bartholomew", 13) == "Hello, Ba...!"


def test_limit_too_small():
    with pytest.raises(ValueError):
        short_hello("Ada", 11)
