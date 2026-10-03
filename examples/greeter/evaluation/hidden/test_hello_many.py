from greeter import hello_many


def test_hello_many():
    assert hello_many("Ada", 2) == "Hello, Ada!\nHello, Ada!"
    assert hello_many("Ada", 1) == "Hello, Ada!"
    assert hello_many("Ada", 0) == ""
