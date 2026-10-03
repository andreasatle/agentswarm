from greeter import farewell


def test_farewell():
    assert farewell("Ada") == "Goodbye, Ada!"
    assert farewell("Grace") == "Goodbye, Grace!"
