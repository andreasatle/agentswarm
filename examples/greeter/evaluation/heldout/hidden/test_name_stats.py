from greeter import name_stats


def test_counts_letters_only():
    assert name_stats("Ada") == "Hello, Ada! Your name has 3 letters."
    assert name_stats("Jo-Ann 2") == "Hello, Jo-Ann 2! Your name has 5 letters."


def test_singular():
    assert name_stats("X") == "Hello, X! Your name has 1 letter."
