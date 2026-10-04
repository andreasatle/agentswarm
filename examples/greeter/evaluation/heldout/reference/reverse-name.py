"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_backwards(name: str) -> str:
    if not name:
        raise ValueError("name must not be empty")
    return hello(name[::-1])
