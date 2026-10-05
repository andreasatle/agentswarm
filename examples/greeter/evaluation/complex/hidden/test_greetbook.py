import json

import pytest
from greetbook import DuplicateName, GreetingBook, load, save


def test_add_get_names_search():
    book = GreetingBook()
    book.add(" Bob ", "Yo")
    book.add("ada", "Hi")
    book.add("Adele", "Hey")
    assert book.get("BOB") == "Yo"
    assert book.names() == ["ada", "Adele", "Bob"]
    assert book.search("AD") == ["ada", "Adele"]
    assert len(book) == 3


def test_duplicates_and_empty():
    book = GreetingBook()
    book.add("Ada", "Hi")
    with pytest.raises(DuplicateName):
        book.add("ADA", "Again")
    assert issubclass(DuplicateName, ValueError)
    with pytest.raises(ValueError):
        book.add("  ", "Hi")


def test_missing():
    book = GreetingBook()
    with pytest.raises(KeyError):
        book.get("nobody")
    with pytest.raises(KeyError):
        book.remove("nobody")


def test_remove():
    book = GreetingBook()
    book.add("Ada", "Hi")
    book.remove("ada")
    assert len(book) == 0


def test_round_trip(tmp_path):
    book = GreetingBook()
    book.add("Bob", "Yo")
    book.add("Ada", "Hi")
    path = tmp_path / "book.json"
    save(book, path)
    data = json.loads(path.read_text())
    assert data == {"entries": [{"name": "Ada", "greeting": "Hi"}, {"name": "Bob", "greeting": "Yo"}]}
    again = load(path)
    assert again.names() == ["Ada", "Bob"] and again.get("bob") == "Yo"


def test_load_missing_and_invalid(tmp_path):
    assert len(load(tmp_path / "none.json")) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        load(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"items": []}))
    with pytest.raises(ValueError):
        load(wrong)
