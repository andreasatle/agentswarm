"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def initials(full_name: str) -> str:
    return "".join(f"{word[0].upper()}." for word in full_name.split())
