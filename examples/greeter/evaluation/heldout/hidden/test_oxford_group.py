import pytest
from greeter import greet_group


def test_one_two_many():
    assert greet_group(["Ada"]) == "Hello, Ada!"
    assert greet_group(["Ada", "Bob"]) == "Hello, Ada and Bob!"
    assert greet_group(["Ada", "Bob", "Cy"]) == "Hello, Ada, Bob, and Cy!"
    assert greet_group(["A", "B", "C", "D"]) == "Hello, A, B, C, and D!"


def test_empty():
    with pytest.raises(ValueError):
        greet_group([])
