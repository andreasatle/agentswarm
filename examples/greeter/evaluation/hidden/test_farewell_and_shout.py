from greeter import farewell, shout


def test_farewell():
    assert farewell("Ada") == "Goodbye, Ada!"


def test_shout():
    assert shout("Ada") == "HELLO, ADA!"
