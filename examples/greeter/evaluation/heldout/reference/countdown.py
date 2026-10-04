"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def countdown_hello(name: str, n: int) -> str:
    if n < 0:
        raise ValueError("n must not be negative")
    return "\n".join([*(f"{i}..." for i in range(n, 0, -1)), hello(name)])
