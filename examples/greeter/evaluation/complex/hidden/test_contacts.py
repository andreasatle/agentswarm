import pytest
from contacts import AddressBook, Contact


@pytest.mark.parametrize(
    "email", ["ada", "ada@", "@x.com", "a@@x.com", "ada@xcom", "ada lovelace@x.com", "a@b@x.com"]
)
def test_invalid_email(email):
    with pytest.raises(ValueError):
        Contact("Ada", email)


def test_empty_name():
    with pytest.raises(ValueError):
        Contact("  ", "ada@x.com")


def test_book():
    book = AddressBook()
    book.add(Contact("Bob", "bob@work.com"))
    book.add(Contact("Ada", "ada@WORK.com"))
    book.add(Contact("ada", "ada@home.org"))
    with pytest.raises(ValueError):
        book.add(Contact("Ada", "ADA@work.com"))
    assert [c.email for c in book.find("ADA")] == ["ada@home.org", "ada@WORK.com"]
    assert [c.name for c in book.by_domain("work.com")] == ["Ada", "Bob"]
    assert book.greet_domain("Work.Com") == ["Hello, Ada!", "Hello, Bob!"]
    assert book.greet_domain("nowhere.net") == []
