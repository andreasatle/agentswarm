from greeter import hello


def test_hello() -> None:
    assert hello("Ada") == "Hello, Ada!"
