"""Reference solution: the seed's greeter.py with this task done."""


def _part(word: str) -> str:
    lower = word.lower()
    if lower.startswith("mc") and len(lower) > 2:
        return "Mc" + lower[2].upper() + lower[3:]
    if lower.startswith("o'") and len(lower) > 2:
        return "O'" + lower[2].upper() + lower[3:]
    return lower[:1].upper() + lower[1:]


def _word(word: str) -> str:
    return "-".join(_part(part) for part in word.split("-"))


def hello(name: str) -> str:
    return f"Hello, {' '.join(_word(w) for w in name.split())}!"
