import pytest
from inventory.report import format_cents


@pytest.mark.parametrize(
    ("cents", "text"),
    [(1234567, "12,345.67"), (99, "0.99"), (100000, "1,000.00"), (0, "0.00"), (-150, "-1.50"), (-5, "-0.05")],
)
def test_format(cents, text):
    assert format_cents(cents) == text
