"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_nick(name: str, nicknames: dict[str, str]) -> str:
    return hello(nicknames.get(name, name))
