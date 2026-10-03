"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_all(names: list[str]) -> list[str]:
    return [hello(name) for name in names]
