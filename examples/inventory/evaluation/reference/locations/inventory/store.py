"""The inventory: items by SKU with their stock per location, and saving and loading as JSON."""

import json
from dataclasses import replace
from pathlib import Path

from inventory.models import Item

MAIN = "main"


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._stock: dict[str, dict[str, int]] = {}  # sku -> location -> quantity

    def add(self, item: Item) -> None:
        """Add a new item, its stock at the main location; a SKU that already exists raises ValueError."""
        if item.sku in self._items:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item
        self._stock[item.sku] = {MAIN: item.quantity} if item.quantity else {}

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item; KeyError if there is none."""
        self._stock.pop(sku, None)
        return self._items.pop(sku)

    def adjust(self, sku: str, delta: int) -> Item:
        """Change the stock at the main location by delta; ValueError if it would go below 0."""
        if delta >= 0:
            self.receive(sku, MAIN, delta)
        else:
            self.ship(sku, MAIN, -delta)
        return self.get(sku)

    def stock_at(self, sku: str, location: str) -> int:
        self.get(sku)
        return self._stock[sku].get(location, 0)

    def locations(self, sku: str) -> dict[str, int]:
        """Where the item is stocked: location -> quantity, without empty locations, sorted by location."""
        self.get(sku)
        return dict(sorted(self._stock[sku].items()))

    def receive(self, sku: str, location: str, quantity: int) -> None:
        self._check(sku, location, quantity)
        self._set(sku, location, self.stock_at(sku, location) + quantity)

    def ship(self, sku: str, location: str, quantity: int) -> None:
        self._check(sku, location, quantity)
        have = self.stock_at(sku, location)
        if quantity > have:
            raise ValueError(f"only {have} of {sku} at {location}")
        self._set(sku, location, have - quantity)

    def transfer(self, sku: str, source: str, target: str, quantity: int) -> None:
        if source == target:
            raise ValueError("source and target are the same location")
        self.ship(sku, source, quantity)
        self.receive(sku, target, quantity)

    def all_locations(self) -> list[str]:
        return sorted({location for stock in self._stock.values() for location in stock})

    def _check(self, sku: str, location: str, quantity: int) -> None:
        self.get(sku)
        if not location.strip():
            raise ValueError("location must not be empty")
        if quantity < 0:
            raise ValueError("quantity must not be negative")

    def _set(self, sku: str, location: str, quantity: int) -> None:
        stock = self._stock[sku]
        if quantity:
            stock[location] = quantity
        else:
            stock.pop(location, None)
        self._items[sku] = replace(self._items[sku], quantity=sum(stock.values()))

    def items(self) -> list[Item]:
        """Every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def save(self, path: str | Path) -> None:
        items = [dict(vars(item), locations=self.locations(item.sku)) for item in self.items()]
        Path(path).write_text(json.dumps({"items": items}, indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        """The inventory saved at path (empty if missing); old files keep all stock at main."""
        inventory = cls()
        file = Path(path)
        if not file.exists():
            return inventory
        data = json.loads(file.read_text())
        for raw in data["items"]:
            fields = dict(raw)
            locations = fields.pop("locations", None)
            inventory.add(Item(**fields))
            if locations is not None:
                inventory._stock[fields["sku"]] = {}
                for location, quantity in locations.items():
                    inventory._set(fields["sku"], location, quantity)
        return inventory
