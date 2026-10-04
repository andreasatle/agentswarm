"""Reference solution: the seed's greeter.py with this task done."""


def hello(name: str) -> str:
    return f"Hello, {name}!"


def secret_hello(name: str, shift: int) -> str:
    def rotate(c: str) -> str:
        if c.isascii() and c.isalpha():
            base = ord("A") if c.isupper() else ord("a")
            return chr(base + (ord(c) - base + shift) % 26)
        return c

    return "".join(rotate(c) for c in hello(name))
