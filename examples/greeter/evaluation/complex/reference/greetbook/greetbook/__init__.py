"""A book of greetings: who gets greeted how."""

from greetbook.book import DuplicateName, GreetingBook
from greetbook.store import load, save

__all__ = ["DuplicateName", "GreetingBook", "load", "save"]
