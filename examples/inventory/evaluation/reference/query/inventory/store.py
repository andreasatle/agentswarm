"""The inventory: items by SKU, and saving and loading them as JSON."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

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

    SORT_KEYS: ClassVar[dict[str, Callable[[Item], tuple[object, ...]]]] = {
        "sku": lambda item: (item.sku,),
        "name": lambda item: (item.name.casefold(), item.sku),
        "quantity": lambda item: (item.quantity, item.sku),
        "price": lambda item: (item.price_cents, item.sku),
        "value": lambda item: (item.value_cents, item.sku),
    }

    def query(
        self,
        name: str | None = None,
        min_quantity: int | None = None,
        max_quantity: int | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        sort: str = "sku",
        descending: bool = False,
        limit: int | None = None,
    ) -> list[Item]:
        """Items matching every given filter (bounds inclusive), sorted, at most limit of them."""
        if sort not in self.SORT_KEYS:
            raise ValueError(f"unknown sort: {sort}")
        for low, high in ((min_quantity, max_quantity), (min_price, max_price)):
            if low is not None and high is not None and low > high:
                raise ValueError("a minimum is above its maximum")
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        if name is not None and not name.strip():
            raise ValueError("name must not be blank")
        found = [
            item
            for item in self._items.values()
            if (name is None or name.casefold() in item.name.casefold())
            and (min_quantity is None or item.quantity >= min_quantity)
            and (max_quantity is None or item.quantity <= max_quantity)
            and (min_price is None or item.price_cents >= min_price)
            and (max_price is None or item.price_cents <= max_price)
        ]
        key = self.SORT_KEYS[sort]
        if descending:
            found.sort(key=lambda item: item.sku)
            found.sort(key=lambda item: key(item)[0], reverse=True)
        else:
            found.sort(key=key)
        return found[:limit] if limit is not None else found

    def __len__(self) -> int:
        return len(self._items)

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
