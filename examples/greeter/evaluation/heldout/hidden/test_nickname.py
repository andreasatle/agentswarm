from greeter import greet_nick


def test_nickname():
    assert greet_nick("Robert", {"Robert": "Bob"}) == "Hello, Bob!"
    assert greet_nick("Ada", {"Robert": "Bob"}) == "Hello, Ada!"
    assert greet_nick("Ada", {}) == "Hello, Ada!"
