import pytest
from greeter import GreetingBook


def test_book():
    book = GreetingBook()
    assert book.greet("Ada") == "Hello, Ada!"
    book.add("es", "Hola, {name}!")
    assert book.greet("Ada", "es") == "Hola, Ada!"
    book.add("en", "Hi, {name}!")
    assert book.greet("Ada") == "Hi, Ada!"


def test_errors():
    book = GreetingBook()
    with pytest.raises(ValueError):
        book.add("fr", "Bonjour!")
    with pytest.raises(KeyError):
        book.greet("Ada", "de")
