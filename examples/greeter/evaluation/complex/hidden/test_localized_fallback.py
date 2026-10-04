import pytest
from greeter import greet_in


def test_languages():
    assert greet_in("Ada", "en") == "Hello, Ada!"
    assert greet_in("Ada", "es") == "¡Hola, Ada!"
    assert greet_in("Ada", "fr") == "Bonjour, Ada !"
    assert greet_in("Ada", "de") == "Hallo, Ada!"


def test_region_falls_back_to_language():
    assert greet_in("Ada", "es-MX") == "¡Hola, Ada!"


def test_unknown_language_falls_back_to_english():
    assert greet_in("Ada", "sv") == "Hello, Ada!"
    assert greet_in("Ada", "sv-SE") == "Hello, Ada!"


@pytest.mark.parametrize("locale", ["ES", "es_MX", "english", "es-mx", "e", ""])
def test_malformed(locale):
    with pytest.raises(ValueError):
        greet_in("Ada", locale)
