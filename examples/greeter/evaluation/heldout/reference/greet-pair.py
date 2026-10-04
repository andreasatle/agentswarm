"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_pair(first: str, second: str) -> str:
    return hello(f"{first} and {second}")
