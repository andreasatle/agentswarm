"""The command line."""

import argparse
import sys

from greetcli import config, styles


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # usage errors: stderr, exit 2 (argparse's default too)
        self.print_usage(sys.stderr)
        self.exit(2, f"error: {message}\n")


def parser() -> Parser:
    top = Parser(prog="greetcli")
    top.add_argument("--config")
    commands = top.add_subparsers(dest="command", required=True, parser_class=Parser)
    hello = commands.add_parser("hello")
    hello.add_argument("name")
    hello.add_argument("--style")
    hello.add_argument("--title")
    bye = commands.add_parser("bye")
    bye.add_argument("name")
    return top


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        defaults = config.load(args.config)
    except config.ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    if args.command == "bye":
        print(styles.bye(args.name))
        return 0
    style = args.style if args.style is not None else defaults.get("style", "plain")
    title = args.title if args.title is not None else defaults.get("title")
    try:
        print(styles.hello(args.name, style, title))
    except styles.StyleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0
