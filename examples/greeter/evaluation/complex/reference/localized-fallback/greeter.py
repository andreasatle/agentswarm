"""A tiny package for the executor to extend."""

import re


def hello(name: str) -> str:
    return f"Hello, {name}!"


LOCALE = re.compile(r"[a-z]{2}(-[A-Z]{2})?")
GREETINGS = {
    "en": "Hello, {name}!",
    "es": "¡Hola, {name}!",
    "fr": "Bonjour, {name} !",
    "de": "Hallo, {name}!",
}


def greet_in(name: str, locale: str) -> str:
    if LOCALE.fullmatch(locale) is None:
        raise ValueError(f"malformed locale: {locale!r}")
    language = locale[:2]
    template = GREETINGS.get(language, GREETINGS["en"])
    return template.format(name=name)
