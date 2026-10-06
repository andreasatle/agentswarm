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


def category_report(inventory: Inventory) -> str:
    """Per category: '== CATEGORY ==', its item lines, 'Subtotal: VALUE'; then 'Total: VALUE'."""
    lines: list[str] = []
    for category in inventory.categories():
        items = [item for item in inventory.items() if item.category == category]
        lines.append(f"== {category} ==")
        lines += [
            f"{item.sku}  {item.name}  {item.quantity} x {format_cents(item.price_cents)} = "
            f"{format_cents(item.value_cents)}"
            for item in items
        ]
        lines.append(f"Subtotal: {format_cents(sum(item.value_cents for item in items))}")
    lines.append(f"Total: {format_cents(sum(item.value_cents for item in inventory.items()))}")
    return "\n".join(lines)
