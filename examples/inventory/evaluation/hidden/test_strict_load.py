import json

import pytest
from inventory import Inventory

GOOD = {"sku": "A1", "name": "Bolt", "quantity": 3, "price_cents": 25}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"things": []}),
        json.dumps([]),
        json.dumps({"items": {}}),
        json.dumps({"items": ["A1"]}),
        json.dumps({"items": [{"sku": "A1"}]}),
        json.dumps({"items": [dict(GOOD, extra=1)]}),
        json.dumps({"items": [dict(GOOD, quantity="3")]}),
        json.dumps({"items": [dict(GOOD, price_cents=2.5)]}),
        json.dumps({"items": [dict(GOOD, quantity=True)]}),
        json.dumps({"items": [dict(GOOD, name=7)]}),
        json.dumps({"items": [dict(GOOD, quantity=-1)]}),
        json.dumps({"items": [GOOD, GOOD]}),
    ],
)
def test_every_malformed_file_is_a_value_error(tmp_path, content):
    path = tmp_path / "inv.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        Inventory.load(path)


def test_message_names_the_entry(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text(json.dumps({"items": [GOOD, dict(GOOD, sku="B2"), dict(GOOD, sku="C3", quantity="x")]}))
    with pytest.raises(ValueError, match="entry 2"):
        Inventory.load(path)


def test_good_and_missing_files(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text(json.dumps({"items": [GOOD]}))
    assert Inventory.load(path).get("A1").quantity == 3
    assert len(Inventory.load(tmp_path / "missing.json")) == 0
