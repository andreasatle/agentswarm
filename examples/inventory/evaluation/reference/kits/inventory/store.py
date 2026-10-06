"""The inventory: items by SKU, kits made of items, and saving and loading as JSON."""

import json
from dataclasses import dataclass, replace
from pathlib import Path

from inventory.models import Item


@dataclass(frozen=True)
class Kit:
    sku: str
    name: str
    price_cents: int
    components: dict[str, int]  # item sku -> how many go into one kit


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._kits: dict[str, Kit] = {}

    def add(self, item: Item) -> None:
        """Add a new item; a SKU that already exists (as an item or a kit) raises ValueError."""
        if item.sku in self._items or item.sku in self._kits:
            raise ValueError(f"duplicate sku: {item.sku}")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        """The item with this SKU; KeyError if there is none."""
        return self._items[sku]

    def remove(self, sku: str) -> Item:
        """Remove and return the item; KeyError if there is none, ValueError while a kit uses it."""
        self.get(sku)
        users = sorted(kit.sku for kit in self._kits.values() if sku in kit.components)
        if users:
            raise ValueError(f"{sku} is used by kits: {', '.join(users)}")
        return self._items.pop(sku)

    def adjust(self, sku: str, delta: int) -> Item:
        """Change an item's quantity by delta (negative takes stock out); ValueError below 0."""
        item = self.get(sku)
        updated = replace(item, quantity=item.quantity + delta)
        self._items[sku] = updated
        return updated

    def define_kit(self, sku: str, name: str, price_cents: int, components: dict[str, int]) -> Kit:
        if not sku.strip() or not name.strip():
            raise ValueError("kit sku and name must not be empty")
        if sku in self._items or sku in self._kits:
            raise ValueError(f"duplicate sku: {sku}")
        if price_cents < 0:
            raise ValueError("price must not be negative")
        if not components:
            raise ValueError("a kit needs at least one component")
        for component, count in components.items():
            self.get(component)
            if count < 1:
                raise ValueError(f"{component}: count must be at least 1")
        kit = Kit(sku, name, price_cents, dict(components))
        self._kits[sku] = kit
        return kit

    def kit(self, sku: str) -> Kit:
        return self._kits[sku]

    def kits(self) -> list[Kit]:
        return [self._kits[sku] for sku in sorted(self._kits)]

    def kit_available(self, sku: str) -> int:
        """How many whole kits the component stock allows."""
        kit = self.kit(sku)
        return min(self._items[c].quantity // n for c, n in kit.components.items())

    def sell_kit(self, sku: str, count: int) -> None:
        """Take the components of count kits out of stock, all or nothing."""
        if count < 1:
            raise ValueError("count must be at least 1")
        if count > self.kit_available(sku):
            raise ValueError(f"only {self.kit_available(sku)} of kit {sku} available")
        for component, n in self.kit(sku).components.items():
            self.adjust(component, -n * count)

    def remove_kit(self, sku: str) -> Kit:
        return self._kits.pop(sku)

    def items(self) -> list[Item]:
        """Every item, sorted by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def save(self, path: str | Path) -> None:
        data = {"items": [vars(item) for item in self.items()], "kits": [vars(kit) for kit in self.kits()]}
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        """The inventory saved at path, with its kits; an empty one if the file doesn't exist."""
        inventory = cls()
        file = Path(path)
        if not file.exists():
            return inventory
        data = json.loads(file.read_text())
        for raw in data["items"]:
            inventory.add(Item(**raw))
        for raw in data.get("kits", []):
            inventory.define_kit(raw["sku"], raw["name"], raw["price_cents"], raw["components"])
        return inventory
