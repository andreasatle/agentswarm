"""Plain-text reports of the inventory."""

from inventory.store import Inventory


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
    kits = inventory.kits()
    if kits:
        lines.append("Kits:")
        lines += [f"{kit.sku}  {kit.name}  available {inventory.kit_available(kit.sku)}" for kit in kits]
    return "\n".join(lines)
