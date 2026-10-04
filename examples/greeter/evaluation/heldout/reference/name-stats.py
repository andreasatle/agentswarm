"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def name_stats(name: str) -> str:
    n = sum(c.isalpha() for c in name)
    return f"{hello(name)} Your name has {n} letter{'' if n == 1 else 's'}."
