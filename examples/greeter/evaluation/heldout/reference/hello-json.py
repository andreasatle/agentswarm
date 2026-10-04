"""Reference solution: the seed's greeter.py with this task done."""

import json


def hello(name: str) -> str:
    return f"Hello, {name}!"


def hello_json(name: str) -> str:
    return json.dumps({"greeting": hello(name)})
