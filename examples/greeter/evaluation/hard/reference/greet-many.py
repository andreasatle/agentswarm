"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_many(names: list[str]) -> str:
    if not names:
        raise ValueError("no names")
    if len(names) == 1:
        return hello(names[0])
    return hello(", ".join(names[:-1]) + " and " + names[-1])
