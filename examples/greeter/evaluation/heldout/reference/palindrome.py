"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def greet_palindrome(name: str) -> str:
    letters = name.replace(" ", "").lower()
    greeting = hello(name)
    return greeting + " Your name is a palindrome." if letters == letters[::-1] else greeting
