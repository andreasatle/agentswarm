from greeter import greet_pair


def test_pair():
    assert greet_pair("Ada", "Bob") == "Hello, Ada and Bob!"
