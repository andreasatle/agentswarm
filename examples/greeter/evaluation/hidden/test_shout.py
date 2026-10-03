from greeter import hello, shout


def test_shout():
    assert shout("Ada") == "HELLO, ADA!"
    assert hello("Ada") == "Hello, Ada!"
