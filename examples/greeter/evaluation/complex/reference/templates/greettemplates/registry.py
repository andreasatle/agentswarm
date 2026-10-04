"""Named templates."""

from greettemplates.template import Template


class TemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, Template] = {}

    def register(self, name: str, text: str) -> None:
        if name in self._templates:
            raise ValueError(f"template {name!r} already registered")
        self._templates[name] = Template(text)

    def render(self, template: str, /, **values: object) -> str:
        return self._templates[template].render(**values)

    def names(self) -> list[str]:
        return sorted(self._templates)
