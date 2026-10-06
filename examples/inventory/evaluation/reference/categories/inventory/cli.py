"""The command line: python -m inventory --file PATH COMMAND ...

Commands: add SKU NAME QUANTITY PRICE_CENTS, remove SKU, adjust SKU DELTA, list, report.
Every command loads the file, applies the change and saves it. Errors go to stderr with exit code 1.
"""

import argparse
import sys
from collections.abc import Sequence

from inventory.models import Item
from inventory.report import category_report, format_cents, stock_report
from inventory.store import Inventory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inventory")
    parser.add_argument("--file", default="inventory.json", help="the inventory file")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("sku")
    add.add_argument("name")
    add.add_argument("quantity", type=int)
    add.add_argument("price_cents", type=int)
    add.add_argument("--category", default="general")
    remove = commands.add_parser("remove")
    remove.add_argument("sku")
    adjust = commands.add_parser("adjust")
    adjust.add_argument("sku")
    adjust.add_argument("delta", type=int)
    listing = commands.add_parser("list")
    listing.add_argument("--category")
    report = commands.add_parser("report")
    report.add_argument("--by-category", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inventory = Inventory.load(args.file)
    try:
        if args.command == "add":
            inventory.add(Item(args.sku, args.name, args.quantity, args.price_cents, args.category))
        elif args.command == "remove":
            inventory.remove(args.sku)
        elif args.command == "adjust":
            inventory.adjust(args.sku, args.delta)
        elif args.command == "list":
            for item in inventory.items():
                if args.category and item.category != args.category:
                    continue
                print(f"{item.sku}  {item.name}  {item.quantity}  {format_cents(item.price_cents)}")
        elif args.command == "report":
            print(category_report(inventory) if args.by_category else stock_report(inventory))
    except (KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    inventory.save(args.file)
    return 0
