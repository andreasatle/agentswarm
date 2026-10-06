"""The inventory: items by SKU, orders that reserve stock, and saving and loading as JSON."""

import json
from dataclasses import replace
from pathlib import Path

from inventory.models import Item


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._orders: dict[str, dict[str, int]] = {}  # order id -> sku -> quantity

    def add(self, item: Item) -> None:
        """Add a new item; a SKU that already exists raises ValueError."""
        if item.sku in self._items:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item; KeyError if there is none, ValueError while an order reserves it."""
        self.get(sku)
        if self.reserved(sku):
            raise ValueError(f"{sku} is reserved by an order")
        return self._items.pop(sku)

    def adjust(self, sku: str, delta: int) -> Item:
        """Change an item's quantity by delta; ValueError below 0 or below what orders reserve."""
        item = self.get(sku)
        updated = replace(item, quantity=item.quantity + delta)
        if updated.quantity < self.reserved(sku):
            raise ValueError(f"{sku}: {self.reserved(sku)} are reserved")
        self._items[sku] = updated
        return updated

    def reserved(self, sku: str) -> int:
        return sum(lines.get(sku, 0) for lines in self._orders.values())

    def available(self, sku: str) -> int:
        """In stock and not reserved; KeyError for an unknown SKU."""
        return self.get(sku).quantity - self.reserved(sku)

    def reserve(self, order_id: str, lines: dict[str, int]) -> None:
        """Reserve every line of an order, or none of them."""
        if not order_id.strip():
            raise ValueError("order id must not be empty")
        if order_id in self._orders:
            raise ValueError(f"duplicate order: {order_id}")
        if not lines:
            raise ValueError("an order needs at least one line")
        for sku, quantity in lines.items():
            if quantity <= 0:
                raise ValueError(f"{sku}: quantity must be positive")
            if quantity > self.available(sku):
                raise ValueError(f"{sku}: only {self.available(sku)} available")
        self._orders[order_id] = dict(lines)

    def fulfil(self, order_id: str) -> None:
        """Ship an order: its quantities leave the stock and the reservation ends; KeyError if unknown."""
        lines = self._orders.pop(order_id)
        for sku, quantity in lines.items():
            item = self._items[sku]
            self._items[sku] = replace(item, quantity=item.quantity - quantity)

    def cancel(self, order_id: str) -> None:
        """Release an order's reservation; KeyError if unknown."""
        del self._orders[order_id]

    def orders(self) -> dict[str, dict[str, int]]:
        return {order: dict(sorted(lines.items())) for order, lines in sorted(self._orders.items())}

    def items(self) -> list[Item]:
        """Every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def save(self, path: str | Path) -> None:
        data = {"items": [vars(item) for item in self.items()], "orders": self.orders()}
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        """The inventory saved at path, with its orders; an empty one if the file doesn't exist."""
        inventory = cls()
        file = Path(path)
        if not file.exists():
            return inventory
        data = json.loads(file.read_text())
        for raw in data["items"]:
            inventory.add(Item(**raw))
        for order, lines in data.get("orders", {}).items():
            inventory._orders[order] = dict(lines)
        return inventory
