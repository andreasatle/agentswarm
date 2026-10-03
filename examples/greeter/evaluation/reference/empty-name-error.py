"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    if not name.strip():
        raise ValueError("name must not be empty")
    return f"Hello, {name}!"
