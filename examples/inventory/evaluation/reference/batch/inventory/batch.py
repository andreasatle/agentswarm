"""Batch scripts: many commands from a file, applied all together or not at all."""

import shlex

from inventory.models import Item
from inventory.store import Inventory


class BatchError(ValueError):
    """A line of the script failed; line is its number (1-based) and nothing was applied."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


def apply_lines(inventory: Inventory, lines: list[str]) -> tuple[Inventory, int]:
    """A copy of inventory with every command applied, and how many commands there were.

    Commands: add SKU NAME QUANTITY PRICE_CENTS, remove SKU, adjust SKU DELTA. Blank lines and lines starting
    with # are skipped; quote names with spaces. The given inventory is never changed.
    """
    result = Inventory()
    for item in inventory.items():
        result.add(item)
    applied = 0
    for number, text in enumerate(lines, start=1):
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            words = shlex.split(stripped)
            command, args = words[0], words[1:]
            if command == "add" and len(args) == 4:
                result.add(Item(args[0], args[1], int(args[2]), int(args[3])))
            elif command == "remove" and len(args) == 1:
                result.remove(args[0])
            elif command == "adjust" and len(args) == 2:
                result.adjust(args[0], int(args[1]))
            else:
                raise ValueError(f"not a command: {stripped}")
        except KeyError as error:
            raise BatchError(number, f"unknown sku {error}") from error
        except ValueError as error:
            raise BatchError(number, str(error)) from error
        applied += 1
    return result, applied
