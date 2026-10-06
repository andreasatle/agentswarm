"""Importing items from CSV: every bad row is reported, and nothing is imported unless all rows are good."""

import csv
import io

from inventory.models import Item

COLUMNS = {"sku", "name", "quantity", "price_cents"}


class ImportFailed(ValueError):
    """The CSV can't be imported; errors lists every problem as 'line N: message'."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def from_csv(text: str) -> list[Item]:
    """Items from CSV with columns sku, name, quantity, price_cents in any order (line 1: header)."""
    reader = csv.DictReader(io.StringIO(text))
    if (
        reader.fieldnames is None
        or set(reader.fieldnames) != COLUMNS
        or len(reader.fieldnames) != len(COLUMNS)
    ):
        raise ImportFailed([f"line 1: the columns must be {', '.join(sorted(COLUMNS))}"])
    items: list[Item] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("wrong number of fields")
            item = Item(row["sku"], row["name"], int(row["quantity"]), int(row["price_cents"]))
        except ValueError as error:
            errors.append(f"line {line}: {error}")
            continue
        if item.sku in seen:
            errors.append(f"line {line}: duplicate sku {item.sku}")
            continue
        seen.add(item.sku)
        items.append(item)
    if errors:
        raise ImportFailed(errors)
    return items
