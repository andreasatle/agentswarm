"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_csv(text: str) -> list[str]:
    return [hello(part.strip()) for part in text.split(",") if part.strip()]
