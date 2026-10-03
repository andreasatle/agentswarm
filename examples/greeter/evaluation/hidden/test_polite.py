from greeter import polite


def test_polite():
    assert polite("Ada", "Dr.") == "Hello, Dr. Ada!"
    assert polite("Lovelace", "Countess") == "Hello, Countess Lovelace!"
