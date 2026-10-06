"""The inventory: items by SKU, and saving and loading them as JSON."""

import json
from dataclasses import replace
from pathlib import Path

from inventory.models import Item


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add(self, item: Item) -> None:
        """Add a new item; a SKU that already exists raises ValueError."""
        if item.sku in self._items:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item; KeyError if there is none."""
        return self._items.pop(sku)

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

    def set_price(self, sku: str, price_cents: int) -> Item:
        """Change one item's price; KeyError for an unknown SKU, ValueError for a negative price."""
        item = self.get(sku)
        updated = replace(item, price_cents=price_cents)
        self._items[sku] = updated
        return updated

    def discount(self, percent: int) -> None:
        """Lower every price by percent (0-100), rounding half up to a whole cent."""
        if not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        for sku, item in list(self._items.items()):
            kept = item.price_cents * (100 - percent)
            self._items[sku] = replace(item, price_cents=(kept * 2 + 100) // 200)

    def save(self, path: str | Path) -> None:
        data = {"items": [vars(item) for item in self.items()]}
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
        return inventory
