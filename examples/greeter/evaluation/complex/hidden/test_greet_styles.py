import pytest
from greeter import greet


def test_styles():
    assert greet("Ada") == "Hello, Ada!"
    assert greet("Ada", "plain") == "Hello, Ada!"
    assert greet("Ada", "formal") == "Good day, Ada."
    assert greet("Ada", "formal", "Dr.") == "Good day, Dr. Ada."
    assert greet("Ada", "shout") == "HELLO, ADA!"


def test_name_is_stripped():
    assert greet("  Ada ", "formal") == "Good day, Ada."


@pytest.mark.parametrize("style", ["plain", "formal", "shout"])
def test_empty_name(style):
    with pytest.raises(ValueError):
        greet("   ", style)


def test_unknown_style():
    with pytest.raises(ValueError):
        greet("Ada", "whisper")


@pytest.mark.parametrize("style", ["plain", "shout"])
def test_title_only_with_formal(style):
    with pytest.raises(ValueError):
        greet("Ada", style, "Dr.")
