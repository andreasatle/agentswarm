from greeter import greet_csv


def test_parses():
    assert greet_csv(" Ada, ,Bob ") == ["Hello, Ada!", "Hello, Bob!"]
    assert greet_csv("Cy") == ["Hello, Cy!"]


def test_empty():
    assert greet_csv("") == []
    assert greet_csv(" , ,") == []
