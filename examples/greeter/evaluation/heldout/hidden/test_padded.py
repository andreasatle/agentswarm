import pytest
from greeter import hello_padded


def test_padded():
    assert hello_padded("Ada", 15, "-") == "--Hello, Ada!--"
    assert hello_padded("Ada", 5, "-") == "Hello, Ada!"


@pytest.mark.parametrize("fill", ["", "ab"])
def test_bad_fill(fill):
    with pytest.raises(ValueError):
        hello_padded("Ada", 15, fill)
