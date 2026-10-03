import pytest
from greeter import greet_in


def test_known_languages():
    assert greet_in("Ada", "en") == "Hello, Ada!"
    assert greet_in("Ada", "es") == "Hola, Ada!"
    assert greet_in("Ada", "fr") == "Bonjour, Ada!"


def test_unknown_language():
    with pytest.raises(ValueError):
        greet_in("Ada", "de")
