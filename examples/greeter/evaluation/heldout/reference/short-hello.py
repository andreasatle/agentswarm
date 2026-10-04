"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def short_hello(name: str, limit: int) -> str:
    if limit < 12:
        raise ValueError("limit too small")
    greeting = hello(name)
    if len(greeting) <= limit:
        return greeting
    keep = limit - len("Hello, ...!")
    return hello(name[:keep] + "...")
