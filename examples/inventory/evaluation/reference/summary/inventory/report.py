"""Plain-text reports of the inventory."""

from dataclasses import dataclass

from inventory.store import Inventory


@dataclass(frozen=True)
class Summary:
    items: int
    units: int
    value_cents: int
    top_sku: str | None


def summarize(inventory: Inventory) -> Summary:
    items = inventory.items()
    top = min(items, key=lambda item: (-item.value_cents, item.sku)) if items else None
    return Summary(
        items=len(items),
        units=sum(item.quantity for item in items),
        value_cents=sum(item.value_cents for item in items),
        top_sku=top.sku if top is not None else None,
    )


def format_cents(cents: int) -> str:
    """12345 -> '123.45'."""
    return f"{cents // 100}.{cents % 100:02d}"


def stock_report(inventory: Inventory) -> str:
    """One line per item ('SKU  NAME  QUANTITY x PRICE = VALUE'), then 'Total: VALUE'."""
    lines = [
        f"{item.sku}  {item.name}  {item.quantity} x {format_cents(item.price_cents)} = "
        f"{format_cents(item.value_cents)}"
        for item in inventory.items()
    ]
    total = sum(item.value_cents for item in inventory.items())
    lines.append(f"Total: {format_cents(total)}")
    return "\n".join(lines)
