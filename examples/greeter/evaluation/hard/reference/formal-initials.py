"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


PARTICLES = {"van", "von", "de", "da", "der"}


def formal_initials(full_name: str) -> str:
    letters = []
    for word in full_name.split():
        first = word[0]
        letters.append((first.lower() if word.lower() in PARTICLES else first.upper()) + ".")
    return " ".join(letters)
