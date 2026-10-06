"""Items in the inventory."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """One stock-keeping unit: a unique code, a name, how many are in stock, and the unit price in cents."""

    sku: str
    name: str
    quantity: int
    price_cents: int

    def __post_init__(self) -> None:
        if not self.sku or not self.sku.strip():
            raise ValueError("sku must not be empty")
        if not self.name or not self.name.strip():
            raise ValueError("name must not be empty")
        if self.quantity < 0:
            raise ValueError("quantity must not be negative")
        if self.price_cents < 0:
            raise ValueError("price must not be negative")

    @property
    def value_cents(self) -> int:
        """What the stock of this item is worth."""
        return self.quantity * self.price_cents
