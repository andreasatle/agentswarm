"""The inventory: items by SKU, a history of changes that can be undone, and saving and loading as JSON."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from inventory.models import Item

HISTORY_LIMIT = 50


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._history: list[dict[str, Any]] = []

    def add(self, item: Item) -> None:
        """Add a new item; a SKU that already exists raises ValueError."""
        if item.sku in self._items:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item
        self._remember({"action": "add", "sku": item.sku})

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item; KeyError if there is none."""
        item = self._items.pop(sku)
        self._remember({"action": "remove", "item": vars(item)})
        return item

    def adjust(self, sku: str, delta: int) -> Item:
        """Change an item's quantity by delta (negative takes stock out); ValueError below 0."""
        item = self.get(sku)
        updated = replace(item, quantity=item.quantity + delta)
        self._items[sku] = updated
        self._remember({"action": "adjust", "sku": sku, "delta": delta})
        return updated

    def undo(self) -> str:
        """Revert the last change and describe it; ValueError when there is nothing to undo."""
        if not self._history:
            raise ValueError("nothing to undo")
        change = self._history.pop()
        if change["action"] == "add":
            del self._items[change["sku"]]
            return f"add {change['sku']}"
        if change["action"] == "remove":
            item = Item(**change["item"])
            self._items[item.sku] = item
            return f"remove {item.sku}"
        item = self._items[change["sku"]]
        self._items[item.sku] = replace(item, quantity=item.quantity - change["delta"])
        return f"adjust {item.sku} by {change['delta']}"

    def history(self) -> list[str]:
        """The changes that can be undone, oldest first."""
        return [self.describe(change) for change in self._history]

    @staticmethod
    def describe(change: dict[str, Any]) -> str:
        if change["action"] == "add":
            return f"add {change['sku']}"
        if change["action"] == "remove":
            return f"remove {change['item']['sku']}"
        return f"adjust {change['sku']} by {change['delta']}"

    def _remember(self, change: dict[str, Any]) -> None:
        self._history.append(change)
        del self._history[:-HISTORY_LIMIT]

    def items(self) -> list[Item]:
        """Every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def save(self, path: str | Path) -> None:
        data = {"items": [vars(item) for item in self.items()], "history": self._history}
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        """The inventory saved at path, with its history; an empty one if the file doesn't exist."""
        inventory = cls()
        file = Path(path)
        if not file.exists():
            return inventory
        data = json.loads(file.read_text())
        for raw in data["items"]:
            item = Item(**raw)
            inventory._items[item.sku] = item
        inventory._history = list(data.get("history", []))
        return inventory
