"""A tiny package for the executor to extend."""

import re


def hello(name: str) -> str:
    return f"Hello, {name}!"


GREETING = re.compile(r"Hello, ([^!]+)!")


def parse_greeting(text: str) -> str:
    match = GREETING.fullmatch(text)
    if match is None or match.group(1) != match.group(1).strip():
        raise ValueError(f"not a greeting: {text!r}")
    return match.group(1)


def tally(greetings: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for text in greetings:
        try:
            name = parse_greeting(text)
        except ValueError:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def most_greeted(greetings: list[str]) -> str | None:
    counts = tally(greetings)
    if not counts:
        return None
    return min(counts, key=lambda name: (-counts[name], name))
