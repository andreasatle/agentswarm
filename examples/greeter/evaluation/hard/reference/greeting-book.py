"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


class GreetingBook:
    def __init__(self) -> None:
        self._templates = {"en": "Hello, {name}!"}

    def add(self, language: str, template: str) -> None:
        if "{name}" not in template:
            raise ValueError("a template needs {name}")
        self._templates[language] = template

    def greet(self, name: str, language: str = "en") -> str:
        return self._templates[language].replace("{name}", name)
