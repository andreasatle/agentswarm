"""Plain-text reports of the inventory."""

from inventory.store import Inventory


def format_cents(cents: int) -> str:
    """1234567 -> '12,345.67'; negative amounts get a leading minus."""
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{rest:02d}"


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
