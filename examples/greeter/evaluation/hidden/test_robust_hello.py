import pytest
from greeter import hello


def test_default():
    assert hello() == "Hello, World!"


def test_strips():
    assert hello(" Ada  ") == "Hello, Ada!"


def test_empty_rejected():
    for name in ["", "  "]:
        with pytest.raises(ValueError):
            hello(name)
