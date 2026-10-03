"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


GREETINGS = {"en": "Hello", "es": "Hola", "fr": "Bonjour"}


def greet_in(name: str, language: str) -> str:
    if language not in GREETINGS:
        raise ValueError(f"unknown language {language!r}")
    return f"{GREETINGS[language]}, {name}!"
