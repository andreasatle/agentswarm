"""The greeting styles."""

STYLES = ("plain", "formal", "shout")


class StyleError(ValueError):
    """An unknown style, or a title with a style that has none."""


def hello(name: str, style: str = "plain", title: str | None = None) -> str:
    if style not in STYLES:
        raise StyleError(f"unknown style: {style}")
    if title and style != "formal":
        raise StyleError("a title needs the formal style")
    if style == "formal":
        return f"Good day, {title} {name}." if title else f"Good day, {name}."
    if style == "shout":
        return f"HELLO, {name.upper()}!"
    return f"Hello, {name}!"


def bye(name: str) -> str:
    return f"Goodbye, {name}!"
