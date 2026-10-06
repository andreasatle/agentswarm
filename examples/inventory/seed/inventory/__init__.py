"""A small inventory: items, a store that saves them as JSON, reports and a command line."""

from inventory.models import Item
from inventory.store import Inventory

__all__ = ["Inventory", "Item"]
