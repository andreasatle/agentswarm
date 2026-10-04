"""A tiny package for the executor to extend."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


STYLES = ("plain", "formal", "shout")


def greet(name: str, style: str = "plain", title: str | None = None) -> str:
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    if style not in STYLES:
        raise ValueError(f"unknown style: {style}")
    if title is not None and style != "formal":
        raise ValueError("a title is only allowed with the formal style")
    if style == "formal":
        who = f"{title} {name}" if title else name
        return f"Good day, {who}."
    if style == "shout":
        return hello(name).upper()
    return hello(name)
