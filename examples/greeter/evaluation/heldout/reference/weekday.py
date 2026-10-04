"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def greet_weekday(name: str, day: int) -> str:
    if not 0 <= day <= 6:
        raise ValueError("day must be 0 (Monday) to 6 (Sunday)")
    return f"Happy {DAYS[day]}, {name}!"
