"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def ascii_hello(name: str) -> str:
    kept = "".join(c for c in name if c.isascii())
    if not kept:
        raise ValueError("no ASCII characters in the name")
    return hello(kept)
