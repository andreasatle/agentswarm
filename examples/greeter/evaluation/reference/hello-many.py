"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def hello_many(name: str, times: int) -> str:
    return "\n".join(hello(name) for _ in range(times))
