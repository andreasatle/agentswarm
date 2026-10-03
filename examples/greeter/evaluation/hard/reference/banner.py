"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def banner(name: str, width: int) -> str:
    greeting = hello(name)
    if width < len(greeting) + 4:
        raise ValueError("too narrow for the greeting")
    return "\n".join(["*" * width, "*" + greeting.center(width - 2) + "*", "*" * width])
