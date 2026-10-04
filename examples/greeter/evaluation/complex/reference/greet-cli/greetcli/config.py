"""Defaults from a JSON config file."""

import json
from pathlib import Path


class ConfigError(Exception):
    """The config file is missing or invalid."""


def load(path: str | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read config {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"config {path} must be a JSON object")
    return {k: v for k, v in data.items() if k in ("style", "title") and isinstance(v, str)}
