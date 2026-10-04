"""A tiny package for the executor to extend."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


PARTICLES = {"van", "von", "de", "der", "la"}


def _capitalise(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))


def normalize_name(raw: str) -> str:
    words = raw.split()
    if not words:
        raise ValueError("name must not be empty")
    normalized = []
    for index, word in enumerate(words):
        if index > 0 and word.lower() in PARTICLES:
            normalized.append(word.lower())
        else:
            normalized.append(_capitalise(word))
    return " ".join(normalized)


def greet_normalized(raw: str) -> str:
    return hello(normalize_name(raw))
