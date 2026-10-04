"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def hello_times(name: str, n: int) -> str:
    if n < 1:
        raise ValueError("n must be at least 1")
    return " ".join([hello(name)] * n)
