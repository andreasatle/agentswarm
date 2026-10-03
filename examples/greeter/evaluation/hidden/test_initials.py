from greeter import initials


def test_initials():
    assert initials("Ada Lovelace") == "A.L."
    assert initials("grace brewster hopper") == "G.B.H."
