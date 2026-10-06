"""Plain-text reports of the inventory."""

from inventory.store import Inventory


def format_cents(cents: int) -> str:
    """12345 -> '123.45'."""
    return f"{cents // 100}.{cents % 100:02d}"


SORTS = {
    "sku": lambda item: item.sku,
    "name": lambda item: (item.name.casefold(), item.sku),
    "value": lambda item: (-item.value_cents, item.sku),
}


def stock_report(inventory: Inventory, by: str = "sku") -> str:
    """One line per item, sorted by sku, name or value, then the total."""
    if by not in SORTS:
        raise ValueError(f"unknown sort: {by}")
    lines = [
        f"{item.sku}  {item.name}  {item.quantity} x {format_cents(item.price_cents)} = "
        f"{format_cents(item.value_cents)}"
        for item in sorted(inventory.items(), key=SORTS[by])
    ]
    total = sum(item.value_cents for item in inventory.items())
    lines.append(f"Total: {format_cents(total)}")
    return "\n".join(lines)
