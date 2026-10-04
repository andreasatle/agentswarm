"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def hello_padded(name: str, width: int, fill: str) -> str:
    if len(fill) != 1:
        raise ValueError("fill must be one character")
    return hello(name).center(width, fill)
