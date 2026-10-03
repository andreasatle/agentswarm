from greeter import hello


def test_names():
    assert hello("ada lovelace") == "Hello, Ada Lovelace!"
    assert hello("mcdonald") == "Hello, McDonald!"
    assert hello("o'neil") == "Hello, O'Neil!"
    assert hello("jean-luc picard") == "Hello, Jean-Luc Picard!"
    assert hello("GRACE") == "Hello, Grace!"
