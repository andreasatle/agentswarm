from greeter import count_greetings


def test_counts():
    assert count_greetings("Hello, Ada! hi Hello, Bob!") == 2
    assert count_greetings("Hello, !") == 0
    assert count_greetings("Hello, R2D2!") == 0
    assert count_greetings("Hello,Ada!") == 0
    assert count_greetings("") == 0
    assert count_greetings("Hello, Ada!Hello, Bob!") == 2
