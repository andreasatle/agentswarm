import pytest
from greeter import most_greeted, parse_greeting, tally


def test_parse():
    assert parse_greeting("Hello, Ada!") == "Ada"
    assert parse_greeting("Hello, Ada Lovelace!") == "Ada Lovelace"


@pytest.mark.parametrize(
    "text", ["Hello Ada!", "Hello, !", "Hello,  Ada!", "Hi, Ada!", "Hello, Ada", "Hello, A!da!"]
)
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        parse_greeting(text)


def test_tally_skips_invalid():
    assert tally(["Hello, Ada!", "nope", "Hello, Bob!", "Hello, Ada!"]) == {"Ada": 2, "Bob": 1}


def test_most_greeted_breaks_ties_alphabetically():
    assert most_greeted(["Hello, Bob!", "Hello, Ada!"]) == "Ada"
    assert most_greeted(["Hello, Bob!", "Hello, Ada!", "Hello, Bob!"]) == "Bob"


def test_most_greeted_none():
    assert most_greeted([]) is None
    assert most_greeted(["garbage"]) is None
