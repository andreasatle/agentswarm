from greeter import hello


def test_strips_whitespace():
    assert hello("  Ada ") == "Hello, Ada!"
    assert hello("Ada") == "Hello, Ada!"
