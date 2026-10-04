import pytest
from greeter import hello_times


def test_repeats():
    assert hello_times("Ada", 1) == "Hello, Ada!"
    assert hello_times("Ada", 3) == "Hello, Ada! Hello, Ada! Hello, Ada!"


@pytest.mark.parametrize("n", [0, -2])
def test_too_few(n):
    with pytest.raises(ValueError):
        hello_times("Ada", n)
