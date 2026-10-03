"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str = "World") -> str:
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    return f"Hello, {name}!"
