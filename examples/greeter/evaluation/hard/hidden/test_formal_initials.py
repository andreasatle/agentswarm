from greeter import formal_initials


def test_initials():
    assert formal_initials("Ludwig van Beethoven") == "L. v. B."
    assert formal_initials("ada lovelace") == "A. L."
    assert formal_initials("Leonardo da Vinci") == "L. d. V."
    assert formal_initials("Johann  Sebastian Bach") == "J. S. B."
