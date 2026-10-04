import json

from greeter import hello_json


def test_json():
    assert json.loads(hello_json("Ada")) == {"greeting": "Hello, Ada!"}
    assert json.loads(hello_json('Bo "B"')) == {"greeting": 'Hello, Bo "B"!'}
