"""A template with {field} placeholders and {{ }} escapes."""

import re

PART = re.compile(r"\{\{|\}\}|\{(\w+)\}")


def fields(text: str) -> set[str]:
    return {m.group(1) for m in PART.finditer(text) if m.group(1)}


def fill(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        return values[match.group(1)]

    return PART.sub(replace, text)
