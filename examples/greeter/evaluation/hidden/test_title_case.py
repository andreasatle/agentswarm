from greeter import hello


def test_title_case():
    assert hello("ada lovelace") == "Hello, Ada Lovelace!"
    assert hello("GRACE") == "Hello, Grace!"
