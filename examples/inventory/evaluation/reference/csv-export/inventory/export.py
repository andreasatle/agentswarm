"""Exporting the inventory as CSV."""

import csv
import io

from inventory.store import Inventory

HEADER = ["sku", "name", "quantity", "price_cents"]


def to_csv(inventory: Inventory) -> str:
    """A header, then one row per item sorted by SKU."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    for item in inventory.items():
        writer.writerow([item.sku, item.name, item.quantity, item.price_cents])
    return out.getvalue()
