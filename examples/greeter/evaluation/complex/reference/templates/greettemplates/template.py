"""A template: literal text and {field} placeholders."""

import re

FIELD = re.compile(r"\w+")


class MissingField(KeyError):
    """A field the template needs but no value was given for."""


class Template:
    def __init__(self, text: str) -> None:
        self._parts: list[tuple[str, str]] = []  # ("text", literal) or ("field", name)
        literal: list[str] = []
        i = 0
        while i < len(text):
            char = text[i]
            if text.startswith("{{", i) or text.startswith("}}", i):
                literal.append(char)
                i += 2
            elif char == "{":
                end = text.find("}", i)
                if end == -1:
                    raise ValueError("unclosed '{'")
                name = text[i + 1 : end]
                if FIELD.fullmatch(name) is None:
                    raise ValueError(f"invalid field name: {name!r}")
                self._parts.append(("text", "".join(literal)))
                literal = []
                self._parts.append(("field", name))
                i = end + 1
            elif char == "}":
                raise ValueError("stray '}'")
            else:
                literal.append(char)
                i += 1
        self._parts.append(("text", "".join(literal)))

    def fields(self) -> list[str]:
        seen: list[str] = []
        for kind, value in self._parts:
            if kind == "field" and value not in seen:
                seen.append(value)
        return seen

    def render(self, **values: object) -> str:
        out: list[str] = []
        for kind, value in self._parts:
            if kind == "text":
                out.append(value)
            elif value in values:
                out.append(str(values[value]))
            else:
                raise MissingField(value)
        return "".join(out)
