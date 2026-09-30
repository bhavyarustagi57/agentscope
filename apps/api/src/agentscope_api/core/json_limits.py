from __future__ import annotations

from typing import Any


def validate_json_complexity(
    value: Any, *, max_depth: int, max_nodes: int, field_name: str = "JSON value"
) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError(f"{field_name} exceeds maximum complexity")
        if depth > max_depth:
            raise ValueError(f"{field_name} exceeds maximum depth")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
