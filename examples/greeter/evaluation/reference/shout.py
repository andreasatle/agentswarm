"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def shout(name: str) -> str:
    return hello(name).upper()
