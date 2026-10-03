"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def parse_greeting(text: str) -> str:
    prefix, suffix = "Hello, ", "!"
    if not (text.startswith(prefix) and text.endswith(suffix)):
        raise ValueError(f"not a greeting: {text!r}")
    name = text[len(prefix) : -len(suffix)]
    if not name or "!" in name or name != name.strip():
        raise ValueError(f"not a greeting: {text!r}")
    return name
