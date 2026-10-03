"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


NUMERALS = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]


def greet_with_age(name: str, age: int) -> str:
    if not isinstance(age, int) or isinstance(age, bool) or not 1 <= age <= 3999:
        raise ValueError("age must be a whole number from 1 to 3999")
    roman, rest = "", age
    for value, letters in NUMERALS:
        while rest >= value:
            roman, rest = roman + letters, rest - value
    return f"Hello, {name}! You are {roman}."
