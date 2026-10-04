import pytest
from greettemplates import MissingField, Template, TemplateRegistry


def test_render_and_fields():
    t = Template("Hello, {name}! You are {age}. Bye, {name}.")
    assert t.fields() == ["name", "age"]
    assert t.render(name="Ada", age=36, extra="ignored") == "Hello, Ada! You are 36. Bye, Ada."


def test_literal_braces():
    assert Template("{{literal}} {x}").render(x=1) == "{literal} 1"


@pytest.mark.parametrize("text", ["Hello {name", "Hello }", "Hi {}", "Hi {a b}", "Hi {a-b}"])
def test_invalid_templates(text):
    with pytest.raises(ValueError):
        Template(text)


def test_missing_field():
    with pytest.raises(MissingField) as caught:
        Template("Hi {name}").render()
    assert caught.value.args[0] == "name"
    assert issubclass(MissingField, KeyError)


def test_registry():
    registry = TemplateRegistry()
    registry.register("hi", "Hi {name}")
    registry.register("bye", "Bye {name}")
    assert registry.names() == ["bye", "hi"]
    assert registry.render("hi", name="Ada") == "Hi Ada"
    with pytest.raises(ValueError):
        registry.register("hi", "again")
    with pytest.raises(KeyError):
        registry.render("nope")
