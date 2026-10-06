"""The command line: python -m inventory --file PATH COMMAND ...

Commands: add SKU NAME QUANTITY PRICE_CENTS, remove SKU, adjust SKU DELTA, list, report.
Every command loads the file, applies the change and saves it. Errors go to stderr with exit code 1.
"""

import argparse
import sys
from collections.abc import Sequence

from inventory.models import Item
from inventory.report import format_cents, location_report, stock_report
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
    remove = commands.add_parser("remove")
    remove.add_argument("sku")
    adjust = commands.add_parser("adjust")
    adjust.add_argument("sku")
    adjust.add_argument("delta", type=int)
    commands.add_parser("list")
    report = commands.add_parser("report")
    report.add_argument("--by-location", action="store_true")
    transfer = commands.add_parser("transfer")
    transfer.add_argument("sku")
    transfer.add_argument("source")
    transfer.add_argument("target")
    transfer.add_argument("quantity", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inventory = Inventory.load(args.file)
    try:
        if args.command == "add":
            inventory.add(Item(args.sku, args.name, args.quantity, args.price_cents))
        elif args.command == "remove":
            inventory.remove(args.sku)
        elif args.command == "adjust":
            inventory.adjust(args.sku, args.delta)
        elif args.command == "list":
            for item in inventory.items():
                print(f"{item.sku}  {item.name}  {item.quantity}  {format_cents(item.price_cents)}")
        elif args.command == "report":
            print(location_report(inventory) if args.by_location else stock_report(inventory))
        elif args.command == "transfer":
            inventory.transfer(args.sku, args.source, args.target, args.quantity)
    except (KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    inventory.save(args.file)
    return 0
