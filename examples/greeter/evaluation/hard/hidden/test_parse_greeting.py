import pytest
from greeter import parse_greeting


def test_parses():
    assert parse_greeting("Hello, Ada!") == "Ada"
    assert parse_greeting("Hello, Ada Lovelace!") == "Ada Lovelace"


def test_rejects_other_forms():
    for text in [
        "Hello Ada!",
        "Hello, Ada",
        "Hello, !",
        "Hi, Ada!",
        "Hello,  Ada!",
        "Hello, Ada!!",
        "hello, Ada!",
    ]:
        with pytest.raises(ValueError):
            parse_greeting(text)
