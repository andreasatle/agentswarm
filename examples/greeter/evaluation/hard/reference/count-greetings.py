"""Reference solution: the seed's greeter.py with this task done."""

import re

GREETING = re.compile(r"Hello, [A-Za-z]+!")


def hello(name: str) -> str:
    return f"Hello, {name}!"


def count_greetings(text: str) -> int:
    return len(GREETING.findall(text))
