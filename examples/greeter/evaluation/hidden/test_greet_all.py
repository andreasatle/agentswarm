from greeter import greet_all


def test_greet_all():
    assert greet_all(["Ada", "Grace"]) == ["Hello, Ada!", "Hello, Grace!"]
    assert greet_all([]) == []
