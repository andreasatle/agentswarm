from greeter import hello


def test_default_name():
    assert hello() == "Hello, World!"
    assert hello("Ada") == "Hello, Ada!"
