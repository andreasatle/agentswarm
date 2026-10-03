"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def count_vowels(text: str) -> int:
    return sum(1 for letter in text.lower() if letter in "aeiou")
