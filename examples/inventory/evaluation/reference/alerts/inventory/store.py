"""The inventory: items by SKU, and saving and loading them as JSON."""

import json
from dataclasses import replace
from pathlib import Path

from inventory.models import Item


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._reorder: dict[str, int] = {}

    def add(self, item: Item) -> None:
        """Add a new item; a SKU that already exists raises ValueError."""
        if item.sku in self._items:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item (and its reorder point); KeyError if there is none."""
        item = self._items.pop(sku)
        self._reorder.pop(sku, None)
        return item

    def set_reorder_point(self, sku: str, point: int) -> None:
        """Warn at or below point; KeyError for an unknown SKU, ValueError if negative."""
        self.get(sku)
        if point < 0:
            raise ValueError("reorder point must not be negative")
        self._reorder[sku] = point

    def reorder_point(self, sku: str) -> int | None:
        return self._reorder.get(sku)

    def alerts(self) -> list[tuple[str, int]]:
        """(sku, shortfall) for items at or below their reorder point, biggest first, then by SKU."""
        low = [
            (sku, point - self._items[sku].quantity)
            for sku, point in self._reorder.items()
            if self._items[sku].quantity <= point
        ]
        return sorted(low, key=lambda entry: (-entry[1], entry[0]))

    def adjust(self, sku: str, delta: int) -> Item:
        """Change an item's quantity by delta (negative takes stock out); ValueError below 0."""
        item = self.get(sku)
        updated = replace(item, quantity=item.quantity + delta)
        self._items[sku] = updated
        return updated

    def items(self) -> list[Item]:
        """Every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def save(self, path: str | Path) -> None:
        data = {
            "items": [vars(item) for item in self.items()],
            "reorder": dict(sorted(self._reorder.items())),
        }
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        """The inventory saved at path; an empty one if the file doesn't exist."""
        inventory = cls()
        file = Path(path)
        if not file.exists():
            return inventory
        data = json.loads(file.read_text())
        for raw in data["items"]:
            inventory.add(Item(**raw))
        for sku, point in data.get("reorder", {}).items():
            inventory.set_reorder_point(sku, point)
        return inventory
