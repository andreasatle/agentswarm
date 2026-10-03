import pytest
from greeter import greet_many


def test_lists():
    assert greet_many(["Ada"]) == "Hello, Ada!"
    assert greet_many(["Ada", "Grace"]) == "Hello, Ada and Grace!"
    assert greet_many(["Ada", "Grace", "Alan"]) == "Hello, Ada, Grace and Alan!"
    assert greet_many(["A", "B", "C", "D"]) == "Hello, A, B, C and D!"


def test_empty():
    with pytest.raises(ValueError):
        greet_many([])
