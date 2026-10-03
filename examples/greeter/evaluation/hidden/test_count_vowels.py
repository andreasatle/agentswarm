from greeter import count_vowels


def test_count_vowels():
    assert count_vowels("Ada Lovelace") == 6
    assert count_vowels("xyz") == 0
    assert count_vowels("AEIOU aeiou") == 10
