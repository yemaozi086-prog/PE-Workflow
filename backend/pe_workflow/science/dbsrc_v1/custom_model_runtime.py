from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def require_dict(value: Any, model_name: str) -> dict:
    """Validate that a user model returned a dictionary."""
    if not isinstance(value, dict):
        raise ValueError(f"{model_name} must return a dict, got {type(value).__name__}.")
    return value


def require_keys(data: Any, required_keys: Iterable[str], model_name: str) -> dict:
    """Validate required top-level keys and return the checked dictionary."""
    checked = require_dict(data, model_name)
    missing = [key for key in required_keys if key not in checked]
    if missing:
        raise ValueError(
            f"{model_name} missing required output keys: {missing}. "
            f"Current keys: {list(checked.keys())}"
        )
    return checked


def require_nested_keys(
    data: Any,
    required_paths: Iterable[Iterable[str]],
    model_name: str,
) -> dict:
    """Validate nested dictionary paths and return the checked dictionary."""
    checked = require_dict(data, model_name)
    missing_paths: list[str] = []
    for path in required_paths:
        path_tuple = tuple(path)
        current: Any = checked
        for key in path_tuple:
            if not isinstance(current, dict) or key not in current:
                missing_paths.append(".".join(path_tuple))
                break
            current = current[key]

    if missing_paths:
        raise ValueError(
            f"{model_name} missing required output paths: {missing_paths}. "
            f"Current top-level keys: {list(checked.keys())}"
        )
    return checked


def call_custom_model(fn: Any, model_name: str, **kwargs: Any) -> dict:
    """Call a custom model with keyword inputs and validate dict output."""
    return require_dict(fn(**kwargs), model_name)
