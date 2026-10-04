import pytest
from greeter import greet_normalized, normalize_name


def test_capitalises_and_collapses():
    assert normalize_name("  ada   LOVELACE ") == "Ada Lovelace"


def test_particles_stay_lower_except_first():
    assert normalize_name("ludwig VAN beethoven") == "Ludwig van Beethoven"
    assert normalize_name("van gogh") == "Van Gogh"
    assert normalize_name("maria de la cruz") == "Maria de la Cruz"


def test_hyphenated_parts():
    assert normalize_name("anne-marie smith-JONES") == "Anne-Marie Smith-Jones"


def test_empty():
    with pytest.raises(ValueError):
        normalize_name(" \t ")


def test_greets_the_normalized_name():
    assert greet_normalized("ada  lovelace") == "Hello, Ada Lovelace!"
