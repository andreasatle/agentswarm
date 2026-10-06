"""The inventory: items by SKU, their stock as cost layers (first in, first out), and saving and loading."""

import json
from dataclasses import replace
from pathlib import Path

from inventory.models import Item


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._layers: dict[str, list[list[int]]] = {}  # sku -> [[quantity, unit cost], ...], oldest first

    def add(self, item: Item) -> None:
        """Add a new item, its stock one layer at its price; ValueError for an existing SKU."""
        if item.sku in self._items:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item
        self._layers[item.sku] = [[item.quantity, item.price_cents]] if item.quantity else []

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item; KeyError if there is none."""
        self._layers.pop(sku, None)
        return self._items.pop(sku)

    def adjust(self, sku: str, delta: int) -> Item:
        """Positive: receive at the item's price; negative: take out oldest first. ValueError below 0."""
        if delta >= 0:
            self.receive(sku, delta, self.get(sku).price_cents)
        else:
            self.sell(sku, -delta)
        return self.get(sku)

    def receive(self, sku: str, quantity: int, unit_cost_cents: int) -> None:
        """New stock at a unit cost; ValueError for a negative quantity or cost."""
        self.get(sku)
        if quantity < 0 or unit_cost_cents < 0:
            raise ValueError("quantity and cost must not be negative")
        if quantity:
            self._layers[sku].append([quantity, unit_cost_cents])
            self._sync(sku)

    def sell(self, sku: str, quantity: int) -> int:
        """Take quantity out, oldest first, and return its cost; ValueError if too little in stock."""
        item = self.get(sku)
        if quantity < 0:
            raise ValueError("quantity must not be negative")
        if quantity > item.quantity:
            raise ValueError(f"only {item.quantity} of {sku} in stock")
        cost = 0
        layers = self._layers[sku]
        while quantity:
            layer = layers[0]
            taken = min(quantity, layer[0])
            cost += taken * layer[1]
            layer[0] -= taken
            quantity -= taken
            if not layer[0]:
                layers.pop(0)
        self._sync(sku)
        return cost

    def cost_value_cents(self, sku: str | None = None) -> int:
        """What the remaining stock cost, for one item or all."""
        skus = [sku] if sku is not None else list(self._items)
        for one in skus:
            self.get(one)
        return sum(q * c for one in skus for q, c in self._layers[one])

    def layers(self, sku: str) -> list[tuple[int, int]]:
        self.get(sku)
        return [(q, c) for q, c in self._layers[sku]]

    def _sync(self, sku: str) -> None:
        self._items[sku] = replace(self._items[sku], quantity=sum(q for q, _ in self._layers[sku]))

    def items(self) -> list[Item]:
        """Every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def save(self, path: str | Path) -> None:
        items = [dict(vars(item), layers=self._layers[item.sku]) for item in self.items()]
        Path(path).write_text(json.dumps({"items": items}, indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        """The inventory saved at path (empty if missing); old files get one layer at the price."""
        inventory = cls()
        file = Path(path)
        if not file.exists():
            return inventory
        data = json.loads(file.read_text())
        for raw in data["items"]:
            fields = dict(raw)
            layers = fields.pop("layers", None)
            inventory.add(Item(**fields))
            if layers is not None:
                inventory._layers[fields["sku"]] = [[q, c] for q, c in layers]
                inventory._sync(fields["sku"])
        return inventory
