from greeter import greet_palindrome


def test_palindromes():
    assert greet_palindrome("Hannah") == "Hello, Hannah! Your name is a palindrome."
    assert greet_palindrome("Ana Na") == "Hello, Ana Na! Your name is a palindrome."


def test_not_a_palindrome():
    assert greet_palindrome("Ada Lovelace") == "Hello, Ada Lovelace!"
