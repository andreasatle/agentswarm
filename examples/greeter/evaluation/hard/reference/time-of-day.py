"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def timed_greeting(name: str, hour: int) -> str:
    if not 0 <= hour <= 23:
        raise ValueError("hour must be from 0 to 23")
    if 5 <= hour <= 11:
        part = "morning"
    elif 12 <= hour <= 17:
        part = "afternoon"
    elif 18 <= hour <= 21:
        part = "evening"
    else:
        part = "night"
    return f"Good {part}, {name}!"
