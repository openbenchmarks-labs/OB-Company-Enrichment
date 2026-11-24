"""Helpers for reading values out of vendor responses whose field names vary."""
from __future__ import annotations

import re
from typing import Any


def first_value(mapping: dict[str, Any], *keys: str) -> Any:
    """The first non-empty value among `keys`. A {"value": ...} wrapper is unwrapped."""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict) and "value" in value:
                value = value["value"]
            return value
    return None


def first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return {}


def as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def parse_employee_range(value: Any) -> tuple[int | None, int | None]:
    """(min, max) from a range object, a number, or text such as "51-200" or "10000+"."""
    if value is None:
        return None, None
    if isinstance(value, dict):
        low = first_value(value, "min", "minimum", "low", "from", "gte", "employees_min")
        high = first_value(value, "max", "maximum", "high", "to", "lte", "employees_max")
        return _int(low), _int(high)
    if isinstance(value, (int, float)):
        number = int(value)
        return number, number
    text = str(value).replace(",", "")
    if text.strip().lower() == "myself only":  # LinkedIn's size label for a one-person company
        return 1, 1
    numbers = [int(v) for v in re.findall(r"\d+", text)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        if "+" in text:
            return numbers[0], None
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def find_first_key(value: Any, key: str) -> Any:
    """Depth-first search for the first non-empty value of `key` in nested dicts and lists."""
    if isinstance(value, dict):
        if key in value and value[key] not in (None, "", [], {}):
            return value[key]
        for child in value.values():
            found = find_first_key(child, key)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_first_key(child, key)
            if found not in (None, "", [], {}):
                return found
    return None
