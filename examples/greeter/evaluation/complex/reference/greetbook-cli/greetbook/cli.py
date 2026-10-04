"""The command line."""

import argparse
import sys

from greetbook import store
from greetbook.book import BookError


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="greetbook")
    top.add_argument("--book", required=True)
    commands = top.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("name")
    add.add_argument("greeting")
    for name in ("get", "remove"):
        commands.add_parser(name).add_argument("name")
    commands.add_parser("list")
    commands.add_parser("search").add_argument("prefix")
    return top


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)  # usage errors exit 2
    try:
        book = store.load(args.book)
        if args.command == "add":
            book.add(args.name, args.greeting)
            store.save(book, args.book)
        elif args.command == "remove":
            book.remove(args.name)
            store.save(book, args.book)
        elif args.command == "get":
            print(book.get(args.name))
        elif args.command == "list":
            for name, greeting in book.items():
                print(f"{name}: {greeting}")
        elif args.command == "search":
            for name in book.search(args.prefix):
                print(name)
    except BookError as error:
        print(error, file=sys.stderr)
        return 1
    return 0
