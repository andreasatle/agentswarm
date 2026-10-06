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
    return "\n".join(lines)


def location_report(inventory: Inventory) -> str:
    """Per location, sorted: '== LOCATION ==' and a line 'SKU  NAME  QUANTITY' for each item stocked there."""
    lines: list[str] = []
    for location in inventory.all_locations():
        lines.append(f"== {location} ==")
        for item in inventory.items():
            quantity = inventory.stock_at(item.sku, location)
            if quantity:
                lines.append(f"{item.sku}  {item.name}  {quantity}")
    return "\n".join(lines)
