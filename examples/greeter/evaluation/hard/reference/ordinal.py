"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_nth(name: str, n: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("n must be a positive whole number")
    teens = n % 100 in (11, 12, 13)  # 11th, 12th, 13th, 111th...
    suffix = "th" if teens else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"Hello, {name}, you are {n}{suffix}!"
